"""Product evaluator: which listing is the best thing to start printing?

eBay is used as the *research* source, the products are sold on Amazon UK
(fees in Settings default to Amazon's). Each product gets a 0-100 opportunity score, a weighted blend of five factors. The
weights are adjustable in the app (they are normalised to add up to 100%):

=====================  =======  ================================================
Factor                 Default  Measures
=====================  =======  ================================================
Demand                 25%      Units sold on the listing.
Market (90 days)       15%      Sold listings across all of eBay in ~90 days -
                                is the *product type* selling right now?
Competition            15%      eBay listings for the product - more means a proven
                                market, since I'm not competing with them on eBay.
Profit margin          15%      Profit / price after Amazon fees and unit costs.
Profit per print hour  15%      Printer time is the real bottleneck.
=====================  =======  ================================================

Values that span orders of magnitude are log-scaled, so going from 10 to 100
sold matters as much as going from 100 to 1,000. Unknown values score a
neutral 50 and raise a flag. Products based on licensed franchises/characters
lose ``ip_risk_penalty`` points. Every factor keeps a plain-English
explanation, so the UI can show *why* something scored well.
"""
from __future__ import annotations

import math
import re
import statistics
from dataclasses import asdict, dataclass, field

from .config import Settings
from .db import Database
from .scraper.http import EbayClient
from .scraper.parsers import parse_item_sold, parse_result_count, parse_search_results, phrase_in

FACTORS = {  # key -> label
    "demand": "Demand",
    "market": "Market (90 days)",
    "competition": "Competition",
    "margin": "Profit margin",
    "efficiency": "Profit per print hour",
}

# title words that say nothing about what the product actually is
STOPWORDS = {
    "a", "an", "and", "the", "for", "with", "of", "to", "in", "on", "by", "uk", "new", "free",
    "p&p", "pp", "post", "postage", "3d", "printed", "print", "printing", "custom", "handmade",
    "quality", "high", "set", "x", "pcs", "pack", "compatible", "fits", "fit", "black", "white",
    "grey", "gray", "red", "blue", "green", "colour", "color", "colours", "or", "&", "-",
}

# Licensed franchises / characters: selling prints of these risks takedowns or legal action.
IP_HIGH_RISK = [
    "disney", "marvel", "pokemon", "pokémon", "pikachu", "star wars", "mandalorian", "grogu",
    "baby yoda", "harry potter", "hogwarts", "lego", "warhammer", "games workshop", "mario",
    "zelda", "minecraft", "fortnite", "batman", "spiderman", "spider-man", "superman",
    "dc comics", "hello kitty", "peppa pig", "paw patrol", "bluey", "stranger things",
    "lord of the rings", "dungeons & dragons", "sonic the hedgehog", "transformers",
    "barbie", "hot wheels", "funko", "labubu", "stanley cup", "f1", "formula 1",
]

# Accessories *for* a licensed product (e.g. a display stand for a LEGO set) are far
# lower risk than reproducing the character or product itself.
ACCESSORY_WORDS = [
    "stand", "stands", "mount", "mounts", "bracket", "brackets", "holder", "holders",
    "display", "case", "hook", "hooks", "adapter", "adaptor", "organiser", "organizer",
    "shelf", "shelves", "rack", "hanger", "dock", "insert", "replacement", "base", "tray",
    "clip", "clips", "storage", "compatible",
]


# per-unit costs and profit for one product
@dataclass
class Costs:
    price: float
    fees: float
    material: float
    electricity: float
    machine: float
    packaging: float
    postage: float
    profit: float
    margin: float
    profit_per_hour: float
    grams: float
    print_hours: float


# one part of the score, e.g. demand
@dataclass
class Factor:
    key: str
    label: str
    score: float  # 0-100
    weight: float  # normalised, 0-1
    detail: str


# everything the product panel shows about the score
@dataclass
class Evaluation:
    score: float
    verdict: str
    factors: list[Factor]
    costs: Costs | None
    ip_risk: str | None = None  # None | "brand" | "high"
    flags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


# turns a count into 0-1: 0 gives 0, full_marks_at or more gives 1, log-scaled in between.
# log scale - 50 vs 500 sold matters, 5000 vs 5500 barely does.
# 1 + value so 0 lands on 0
def _log_scale(value: float | None, full_marks_at: float) -> float:
    if not value or value <= 0:
        return 0.0
    return min(1.0, math.log10(1 + value) / math.log10(1 + full_marks_at))


# does the title name a licensed franchise? returns (risk, the name found).
# risk is "high", "brand" for an accessory for it (lego display stand), or None.
# phrase_in matches whole words, so "f1" doesn't fire inside "af1"
def ip_risk(title: str) -> tuple[str | None, str | None]:
    for term in IP_HIGH_RISK:
        if phrase_in(title, term):
            is_accessory = any(phrase_in(title, w) for w in ACCESSORY_WORDS)
            return ("brand" if is_accessory else "high"), term
    return None, None


# per-unit costs and profit, all in £. grams of filament, hours of printer time
def unit_costs(price: float, grams: float, hours: float, s: Settings) -> Costs:
    # % of the price but never below the per-sale minimum, plus any fixed per-item fee
    # (Amazon UK preset: 15%, min £0.25)
    fees = max(price * s.fee_percent / 100, s.fee_minimum) + s.fee_fixed
    material = grams / 1000 * s.filament_cost_per_kg
    electricity = hours * s.electricity_cost_per_hour
    machine = hours * s.machine_cost_per_hour  # printer wear/parts, spread per hour
    total = fees + material + electricity + machine + s.packaging_cost + s.postage_cost
    profit = price - total
    return Costs(price=price, fees=round(fees, 2), material=round(material, 2),
                 electricity=round(electricity, 2), machine=round(machine, 2),
                 packaging=s.packaging_cost, postage=s.postage_cost, profit=round(profit, 2),
                 margin=round(profit / price, 4) if price else 0.0,
                 profit_per_hour=round(profit / hours, 2) if hours else 0.0,
                 grams=grams, print_hours=hours)


# the word shown next to the score
def verdict_for(score: float) -> str:
    if score >= 70:
        return "Strong opportunity"
    if score >= 55:
        return "Worth testing"
    if score >= 40:
        return "Marginal"
    return "Avoid"


# scores one product (a listing joined with its evaluation inputs)
def evaluate(p: dict, s: Settings) -> Evaluation:
    w = s.weights()
    flags = []   # warnings shown in the product panel
    scores = {}  # factor key -> (score 0-100, explanation)

    grams = p.get("grams") or s.default_grams
    hours = p.get("print_hours") or s.default_print_hours
    if p.get("grams") is None or p.get("print_hours") is None:
        flags.append("Using default print weight/time - enter slicer estimates for accuracy")

    # demand: lifetime sold on this listing, 1,000 sales = full marks
    sold = int(p.get("sold") or 0)
    detail = f"{sold:,} sold on this listing"
    if p.get("sold_checked_at"):
        detail += " (checked on the listing page)"
    else:
        detail += " (from search results - “Check market” confirms it)"
    scores["demand"] = (100 * _log_scale(sold, 1000), detail)

    # market: sold across eBay recently
    market = p.get("market_sold")
    # unknown = neutral 50 throughout, so unchecked products aren't sunk or boosted
    if market is None:
        scores["market"] = (50.0, "Unknown - run a market check")
    else:
        detail = f"{market:,} sold across eBay in ~90 days"
        if p.get("market_median"):
            detail += f" (median £{p['market_median']:.2f})"
        scores["market"] = (100 * _log_scale(market, 300), detail)  # 300 sold in 90 days = full marks

    # competition: I sell on Amazon UK, so lots of eBay listings means proven demand
    # rather than rivals. 5,000 listings = full marks
    competition = p.get("competition")
    if competition is None:
        scores["competition"] = (50.0, "Unknown - run a market check")
    else:
        detail = f"{competition:,} eBay listings - a proven market"
        if p.get("printed_share") is not None:
            detail += f", {p['printed_share']:.0%} of the first page 3D printed"
        if p.get("competitor_median"):
            detail += f", median price £{p['competitor_median']:.2f}"
        scores["competition"] = (100 * _log_scale(competition, 5000), detail)
    if market is None or competition is None:
        flags.append("Market not checked yet - press “Check market” for sales and competition data")

    # profitability
    price = p.get("sell_price") or p.get("price")  # your own price wins over the eBay one
    costs = None
    if not price:
        flags.append("No price captured")
        scores["margin"] = (50.0, "Unknown price")
        scores["efficiency"] = (50.0, "Unknown price")
    else:
        costs = unit_costs(float(price), float(grams), float(hours), s)
        # linear, capped: 60% margin or £6 profit per printer-hour = full marks.
        # losses clamp to 0 here (the score gets capped further down anyway)
        scores["margin"] = (100 * min(1.0, max(0.0, costs.margin) / 0.6),
                            f"{costs.margin:.0%} margin, £{costs.profit:.2f} profit per sale"
                            + (" at your price" if p.get("sell_price") else " at the eBay price"))
        scores["efficiency"] = (100 * min(1.0, max(0.0, costs.profit_per_hour) / 6.0),
                                f"£{costs.profit_per_hour:.2f} per printer-hour ({hours:g} h print)")
        # sold median if we have it (what people actually paid), else active listings
        ref = p.get("market_median") or p.get("competitor_median")
        if ref and float(price) > ref * 1.3:  # 30%+ over typical
            flags.append(f"Priced well above the typical £{ref:.2f} - you may need to undercut, "
                         f"which lowers profit")

    factors = []
    for key, label in FACTORS.items():
        value, detail = scores[key]
        factors.append(Factor(key, label, round(value, 1), round(w[key], 3), detail))
    score = sum(f.score * f.weight for f in factors)  # weights add up to 1, so still 0-100

    # penalties go on after the weighted blend so no weight setting can hide them

    risk, term = ip_risk(p["title"])
    if risk == "high":
        flags.append(f"IP risk: “{term}” is a licensed brand/character - selling prints of it "
                     f"can get listings removed or lead to legal action")
        score -= s.ip_risk_penalty
    elif risk == "brand":
        flags.append(f"Mentions “{term}” - fine for accessories, but say “compatible with” "
                     f"and don't use their logo")

    if costs and costs.profit <= 0:
        flags.append("Loses money at the current price")
        score = min(score, 25.0)  # always lands in "Avoid" however good the demand is

    score = max(0.0, score)
    return Evaluation(score=round(score, 1), verdict=verdict_for(score), factors=factors,
                      costs=costs, ip_risk=risk, flags=flags)


# market research
# the words in a title that say what the product is. drops STOPWORDS and
# sizes/quantities like 10mm, 2pcs, 3x
def title_tokens(title: str) -> list[str]:
    words = re.findall(r"[a-z0-9][a-z0-9\-&']*", title.lower())
    return [w for w in words if w not in STOPWORDS and not re.fullmatch(r"\d+(mm|cm|m|pcs|x)", w)]


# shortens a listing title to a search, e.g. "NEW 3D Printed Hive Stand - Black" -> "hive stand"
def search_query_for(title: str, max_words: int = 6) -> str:
    return " ".join(title_tokens(title)[:max_words]) or title


# searches eBay (current and sold listings) and saves how crowded and how busy the market is
def check_market(client: EbayClient, db: Database, item_id: str, settings: Settings) -> dict:
    product = db.product(item_id)
    if not product:
        raise KeyError(item_id)
    query = search_query_for(product["title"])

    # active listings = competition. leave the product itself out of the competitors
    html = client.search(query)
    items = [i for i in parse_search_results(html) if i.item_id != item_id]
    count = parse_result_count(html)
    if count is None:  # no "N results" header found, fall back to the first page
        count = len(items)
    prices = [i.price for i in items if i.price]
    # only the first page of results, so printed_share is a sample not a full count
    printed = [i for i in items if phrase_in(i.title, settings.required_phrase)]

    # The listing's own page has the true lifetime sold count (cards can under-report,
    # e.g. for listings with colour/size variations)
    try:
        sold = parse_item_sold(client.get(product["url"]))
        if sold is not None:
            db.set_sold(item_id, sold)
    except Exception:
        pass  # listing gone or page changed, keep the card figure and carry on

    # same search with eBay's sold filter, which only goes back ~90 days
    sold_html = client.sold_search(query)
    sold_items = parse_search_results(sold_html)
    sold_count = parse_result_count(sold_html)
    if sold_count is None:
        sold_count = len(sold_items)
    sold_prices = [i.price for i in sold_items if i.price]

    result = {
        "competition": count,
        "competitor_median": round(statistics.median(prices), 2) if prices else None,
        "printed_share": round(len(printed) / len(items), 3) if items else None,
        "market_sold": sold_count,
        "market_median": round(statistics.median(sold_prices), 2) if sold_prices else None,
    }
    db.save_evaluation(item_id, **result)
    # the search used is handy for the log, but isn't saved
    return {**result, "competition_query": query}


# grouping near-duplicate listings
# puts listings with similar titles into groups (Jaccard similarity of title words) and
# adds group_id, group_size and group_sold to each one. several sellers each selling the
# same kind of item is strong evidence of real demand
def group_similar(products: list[dict], threshold: float = 0.5) -> None:
    # union-find: every listing starts as its own group, similar pairs get merged
    tokens = [set(title_tokens(p["title"])) for p in products]
    parent = list(range(len(products)))

    # root of i's group. path halving on the way up keeps the trees flat
    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    # word -> listings containing it, so each title is only compared with ones sharing a
    # word instead of every other title
    index = {}
    for i, words in enumerate(tokens):
        for word in words:
            index.setdefault(word, []).append(i)

    for i, words in enumerate(tokens):
        # the other listings that share a word with this one. words in 300+ titles are
        # too common to mean much and would make this very slow.
        # j > i so each pair is only checked once
        candidates = set()
        for word in words:
            if len(index[word]) <= 300:
                candidates.update(j for j in index[word] if j > i)
        for j in candidates:
            # Jaccard = shared words / all words. 0.5 by default
            shared = len(words & tokens[j])
            total = len(words | tokens[j])
            if shared / total >= threshold:
                parent[find(j)] = find(i)  # join the two groups

    # collect the listings in each group: root -> list of listing positions
    groups = {}
    for i in range(len(products)):
        groups.setdefault(find(i), []).append(i)
    for root, members in groups.items():
        sold = sum(int(products[m].get("sold") or 0) for m in members)
        gid = products[root]["item_id"]  # the group is named after its root listing
        for m in members:
            products[m].update(group_id=gid, group_size=len(members), group_sold=sold)


# keeps the first (best-ranked) listing of each similar-product group
def best_per_group(rows: list[dict]) -> list[dict]:
    seen = set()
    best = []
    for r in rows:
        if r["group_id"] not in seen:
            seen.add(r["group_id"])
            best.append(r)
    return best


# scores every product, groups similar listings and sorts best-first
def rank(products: list[dict], s: Settings) -> list[dict]:
    out = []
    for p in products:
        ev = evaluate(p, s)
        out.append({**p, "score": ev.score, "profit": ev.costs.profit if ev.costs else None,
                    "ip_risk": ev.ip_risk})
    group_similar(out)
    out.sort(key=lambda r: r["score"], reverse=True)
    return out
