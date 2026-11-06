from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

from .config import DB_PATH

SCHEMA = """

CREATE TABLE IF NOT EXISTS scans (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  REAL NOT NULL,
    seeds       TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS listings (
    item_id        TEXT PRIMARY KEY,
    title          TEXT NOT NULL,
    url            TEXT NOT NULL,
    price          REAL,
    sold           INTEGER NOT NULL DEFAULT 0,
    seller         TEXT,
    image_url      TEXT,
    evidence       TEXT,              -- title | description | image
    status         TEXT NOT NULL DEFAULT 'new',   -- new | shortlisted | dismissed
    first_seen     REAL NOT NULL,
    last_seen      REAL NOT NULL
);

-- sellers and searches already done, so later scans skip them
CREATE TABLE IF NOT EXISTS sellers (name TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS searches (term TEXT PRIMARY KEY);

-- Per-product inputs and market data used by the evaluator.
CREATE TABLE IF NOT EXISTS evaluations (
    item_id            TEXT PRIMARY KEY,
    grams              REAL,
    print_hours        REAL,
    competition        INTEGER,
    competitor_median  REAL,
    market_sold        INTEGER,       -- sold listings market-wide in the last ~90 days
    market_median      REAL,          -- median sold price of those
    printed_share      REAL,          -- share of competing listings that are 3D printed
    sell_price         REAL,          -- the price YOU plan to sell at (overrides eBay price)
    sold_checked_at    REAL           -- sold count confirmed on the listing's own page
);


CREATE TABLE IF NOT EXISTS rejected_listings (
    item_id    TEXT PRIMARY KEY,
    title      TEXT NOT NULL,
    image_url  TEXT,
    seller     TEXT,
    reason     TEXT NOT NULL,
    seen_at    REAL NOT NULL
);
"""


# Where a product is in the workflow: new find, shortlisted to make, or dismissed
STATUSES = ("new", "shortlisted", "dismissed")


class Database:
    def __init__(self, path: Path | str = DB_PATH):
        # make the data folder if it doesn't exist yet
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        # one connection shared by the crawler threads
        # the lock makes sure only one thread uses it at a time
        self.connection = sqlite3.connect(str(path), check_same_thread=False)
        self.connection.row_factory = sqlite3.Row  # rows can be turned into dicts
        self.lock = threading.Lock()

        self.version = 0
        with self.lock:
            self.connection.execute("PRAGMA journal_mode=WAL")  # copes better with lots of writes
            self.connection.executescript(SCHEMA)
            self.commit()

    # helpers
    def commit(self) -> None:
        self.connection.commit()
        self.version += 1

    # runs one statement that changes data, then saves it
    def execute(self, sql: str, params: tuple = ()) -> None:
        with self.lock:
            self.connection.execute(sql, params)
            self.commit()

    # runs a SELECT and returns the rows as a list of dicts
    def query(self, sql: str, params: tuple = ()) -> list[dict]:
        with self.lock:
            return [dict(r) for r in self.connection.execute(sql, params).fetchall()]

    # the first row of a SELECT, or None
    def one(self, sql: str, params: tuple = ()) -> dict | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    # number of rows
    def count(self, sql: str, params: tuple = ()) -> int:
        return self.one(sql, params)["n"]

    # scans
    def start_scan(self, seeds: list[str]) -> None:
        self.execute("INSERT INTO scans(started_at, seeds) VALUES (?, ?)", (time.time(), "\n".join(seeds)))

    # listings
    # adds a listing, or updates it if it's already saved
    def upsert_listing(self, *, item_id: str, title: str, url: str, price: float | None,
                       sold: int, seller: str | None, image_url: str | None,
                       evidence: str) -> bool:
        # returns true if listing is new
        now = time.time()
        with self.lock:
            exists = self.connection.execute(
                "SELECT 1 FROM listings WHERE item_id = ?", (item_id,)
            ).fetchone()
            if exists:
                self.connection.execute(
                    """UPDATE listings SET title = ?, url = ?, price = COALESCE(?, price),
                       sold = MAX(sold, ?), seller = COALESCE(?, seller),
                       image_url = COALESCE(?, image_url), last_seen = ? WHERE item_id = ?""",
                    (title, url, price, sold, seller, image_url, now, item_id),
                )
            else:
                self.connection.execute(
                    """INSERT INTO listings(item_id, title, url, price, sold, seller, image_url,
                       evidence, first_seen, last_seen)
                       VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (item_id, title, url, price, sold, seller, image_url, evidence, now, now),
                )
            self.commit()
            return not exists

    def set_listing_status(self, item_id: str, status: str) -> None:
        if status not in STATUSES:
            raise ValueError(f"invalid status: {status}")
        self.execute("UPDATE listings SET status = ? WHERE item_id = ?", (status, item_id))

    # joins data to get all proudcts info
    PRODUCT_SQL = """
        SELECT l.*, e.grams, e.print_hours, e.competition, e.competitor_median,
               e.market_sold, e.market_median, e.printed_share, e.sell_price,
               e.sold_checked_at
        FROM listings l
        LEFT JOIN evaluations e ON e.item_id = l.item_id"""

    def products(self) -> list[dict]:
        return self.query(self.PRODUCT_SQL)

    def product(self, item_id: str) -> dict | None:
        return self.one(self.PRODUCT_SQL + " WHERE l.item_id = ?", (item_id,))

    # corrects a listing's sold count from its own page
    def set_sold(self, item_id: str, sold: int) -> None:
        with self.lock:
            self.connection.execute("UPDATE listings SET sold = ? WHERE item_id = ?", (sold, item_id))
            self.connection.execute("INSERT OR IGNORE INTO evaluations(item_id) VALUES (?)", (item_id,))
            self.connection.execute("UPDATE evaluations SET sold_checked_at = ? WHERE item_id = ?",
                               (time.time(), item_id))
            self.commit()

    # what an earlier scan decided about a listing, printed or not
    def known_verdict(self, item_id: str) -> tuple[bool, str] | None:
        row = self.one("SELECT evidence FROM listings WHERE item_id = ?", (item_id,))
        if row:
            return True, row["evidence"]
        if self.one("SELECT item_id FROM rejected_listings WHERE item_id = ?", (item_id,)):
            return False, "none"
        return None

    def known_search_terms(self) -> set[str]:
        return {" ".join(r["term"].lower().split()) for r in self.query("SELECT term FROM searches")}

    def known_sellers(self) -> set[str]:
        return {r["name"] for r in self.query("SELECT name FROM sellers")}

    # a negative training photo
    def record_rejected(self, item_id: str, title: str, image_url: str | None, seller: str | None) -> None:
        self.execute(
            """INSERT OR REPLACE INTO rejected_listings(item_id, title, image_url, seller, reason, seen_at)
               VALUES (?, ?, ?, ?, 'none', ?)""",
            (item_id, title, image_url, seller, time.time()),
        )

    # how many listing photos are available to auto-build a training set
    def training_sources(self) -> dict:
    
        positives = self.count("""SELECT COUNT(*) AS n FROM listings WHERE image_url IS NOT NULL
                                  AND evidence IN ('title', 'description') AND status != 'dismissed'""")
        negatives = self.count("SELECT COUNT(*) AS n FROM rejected_listings WHERE image_url IS NOT NULL")
        return {"positives": positives, "negatives": negatives}

    # saves estimates or market check results for a product
    def save_evaluation(self, item_id: str, **values) -> None:
        allowed = {"grams", "print_hours", "competition", "competitor_median", "market_sold",
                   "market_median", "printed_share", "sell_price"}


        cols = ", ".join(f"{k} = ?" for k in values)
        with self.lock:

            self.connection.execute("INSERT OR IGNORE INTO evaluations(item_id) VALUES (?)", (item_id,))
            self.connection.execute(f"UPDATE evaluations SET {cols} WHERE item_id = ?",
                               (*values.values(), item_id))
            self.commit()

    # sellers / searches
    def record_seller(self, name: str) -> None:
        self.execute("INSERT OR IGNORE INTO sellers(name) VALUES (?)", (name,))

    def record_search(self, term: str) -> None:
        self.execute("INSERT OR IGNORE INTO searches(term) VALUES (?)", (term,))

    # numbers for the Overview page
    def stats(self) -> dict:
        return {
            "listings": self.count("SELECT COUNT(*) AS n FROM listings WHERE status != 'dismissed'"),
            "shortlisted": self.count("SELECT COUNT(*) AS n FROM listings WHERE status = 'shortlisted'"),
            "sellers": self.count("SELECT COUNT(*) AS n FROM sellers"),
            "scans": self.count("SELECT COUNT(*) AS n FROM scans"),
        }
