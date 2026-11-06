"""Crawler tests use a fake eBay client, so they run offline and deterministically."""
from printscout.config import Settings
from printscout.db import Database
from printscout.scraper.crawler import Crawler
from printscout.scraper.http import EbayClient


def card(item_id, title, seller, feedback=500, sold=10, price="£5.00"):
    return (f'<li class="s-item"><a class="s-item__link" href="https://www.ebay.co.uk/itm/{item_id}">'
            f'<div class="s-item__title">{title}</div></a><span class="s-item__price">{price}</span>'
            f'<span>{sold} sold</span><span class="s-item__seller-info-text">{seller} ({feedback}) 99.9%</span></li>')


class FakeClient(EbayClient):
    def __init__(self, searches, sellers, descriptions=None):
        super().__init__(delay=0)
        self.searches, self.sellers, self.descriptions = searches, sellers, descriptions or {}
        self.calls = []

    def search(self, term):
        self.calls.append(("search", term))
        return "<ul>" + "".join(self.searches.get(term.lower(), [])) + "</ul>"

    def seller_listings(self, seller):
        self.calls.append(("seller", seller))
        return "<ul>" + "".join(self.sellers.get(seller, [])) + "</ul>"

    def description(self, item_id):
        self.calls.append(("desc", item_id))
        return self.descriptions.get(item_id, "<p>nothing here</p>")

    def get(self, url, *, binary=False):
        self.calls.append(("page", url))
        return self.pages.get(url, "<p>listing page</p>") if hasattr(self, "pages") else "<p>listing page</p>"


def make(settings=None, **kw):
    s = settings or Settings(workers=3, request_delay=0, max_sellers=10)
    db = Database(":memory:")
    client = FakeClient(**kw)
    return Crawler(client, db, s), db, client


def test_snowball_discovers_new_sellers():
    crawler, db, client = make(
        searches={
            "stand": [card(111111111, "3D printed stand", "alice"),
                      card(222222222, "Plastic stand", "bob")],  # not 3D printed → bob ignored
            "3d printed hook": [card(333333333, "3D Printed hook", "carol")],
        },
        sellers={
            "alice": [card(444444444, "3D printed hook", "alice", sold=120),
                      card(555555555, "Metal hook", "alice")],
            "carol": [card(666666666, "3D printed cable tidy", "carol", sold=40)],
        },
    )
    stats = crawler.run(["stand"])
    ids = {r["item_id"] for r in db.products()}
    assert ids == {"444444444", "666666666"}  # carol found by searching alice's listing title
    assert stats.sellers_scanned == 2
    assert ("seller", "bob") not in client.calls
    assert db.product("444444444")["sold"] == 120


def test_low_feedback_sellers_skipped():
    s = Settings(workers=2, request_delay=0, min_seller_feedback=100)
    crawler, db, client = make(s, searches={"x": [card(111111111, "3D printed x", "newbie", feedback=5)]},
                               sellers={})
    crawler.run(["x"])
    assert not any(c[0] == "seller" for c in client.calls)


def test_description_check_and_exclusions():
    crawler, db, _ = make(
        searches={"x": [card(111111111, "Phone stand", "dave")]},
        sellers={"dave": [card(222222222, "Desk tidy", "dave"), card(333333333, "Laser cut sign", "dave")]},
        descriptions={"111111111": "<p>3D printed in PLA</p>", "222222222": "<p>Made on a 3D-printer, 3d printed</p>",
                      "333333333": "<p>3d printed</p>"},
    )
    crawler.run(["x"])
    rows = {r["item_id"]: r for r in db.products()}
    assert set(rows) == {"222222222"}  # 333 is excluded by 'laser' in the title
    assert rows["222222222"]["evidence"] == "description"


def test_seller_budget_respected():
    searches = {"x": [card(100000000 + i, "3D printed thing", f"seller{i}") for i in range(20)]}
    sellers = {f"seller{i}": [card(900000000 + i, "3D printed widget", f"seller{i}")] for i in range(20)}
    crawler, db, client = make(Settings(workers=4, request_delay=0, max_sellers=5),
                               searches=searches, sellers=sellers)
    stats = crawler.run(["x"])
    assert stats.sellers_scanned == 5
    assert sum(1 for c in client.calls if c[0] == "seller") == 5


def test_image_detector_used_when_text_is_silent():
    class Det:
        def predict_bytes(self, data):
            return 0.9

    s = Settings(workers=1, request_delay=0, use_image_detector=True, check_descriptions=False)
    db = Database(":memory:")
    client = FakeClient(searches={"x": [card(111111111, "Phone stand", "erin").replace(
        '<a ', '<img src="https://i.ebayimg.com/images/g/a/s-l140.jpg"><a ')]},
        sellers={"erin": []})
    client.image = lambda url: b"img"
    crawler = Crawler(client, db, s, detector=Det())
    crawler.run(["x"])
    assert ("seller", "erin") in client.calls


def test_check_market_records_sales_and_competition():
    from printscout.evaluator import check_market
    db = Database(":memory:")
    db.upsert_listing(item_id="999999999", title="3D Printed Hive Thermostat Stand", url="u", price=6.0,
                      sold=100, seller="me", image_url=None, evidence="title")

    class C(FakeClient):
        def sold_search(self, term):
            self.calls.append(("sold", term))
            return ('<h1 class="srp-controls__count-heading">87 results</h1><ul>'
                    + card(111111111, "Hive stand", "x", price="£5.00")
                    + card(222222222, "Hive stand", "y", price="£7.00") + "</ul>")
    c = C(searches={"hive thermostat stand": [card(333333333, "3D printed hive stand", "z", price="£6.50"),
                                              card(444444444, "Metal hive bracket", "w", price="£9.00")]},
          sellers={})
    c.pages = {"u": '<div class="x-quantity__availability"><span>1,234 sold</span></div>'}
    r = check_market(c, db, "999999999", Settings())
    assert db.product("999999999")["sold"] == 1234
    assert r["competition_query"] == "hive thermostat stand"
    assert r["market_sold"] == 87 and r["market_median"] == 6.0
    assert r["competition"] == 2 and r["printed_share"] == 0.5
    assert db.product("999999999")["market_sold"] == 87


def test_rejected_listings_become_negatives():
    s = Settings(workers=1, request_delay=0, max_sellers=5)
    db = Database(":memory:")
    img = '<img src="https://i.ebayimg.com/images/g/a/s-l140.jpg">'
    client = FakeClient(
        searches={"x": [card(111111111, "3D printed stand", "dave")]},
        sellers={"dave": [card(222222222, "Metal stand", "dave").replace("<a ", img + "<a "),
                          card(333333333, "Movie prop helmet", "dave").replace("<a ", img + "<a "),
                          card(444444444, "3D printed hook", "dave").replace("<a ", img + "<a ")]},
        descriptions={"222222222": "<p>Solid steel, powder coated</p>"})
    Crawler(client, db, s).run(["x"])
    rejected = {r["item_id"]: r for r in db.query("SELECT * FROM rejected_listings")}
    assert set(rejected) == {"222222222"}  # 'prop' is excluded but may be printed → not a negative
    assert rejected["222222222"]["seller"] == "dave"
    assert db.training_sources() == {"positives": 1, "negatives": 1}


def _world():
    return dict(
        searches={"stand": [card(111111111, "3D printed stand", "alice"),
                            card(121111111, "3D printed stand", "bob")],
                  "3d printed hook": [card(131111111, "3D printed hook", "carol")]},
        sellers={"alice": [card(444444444, "3D printed hook", "alice", sold=10)],
                 "bob": [card(454444444, "3D printed shelf", "bob", sold=3)],
                 "carol": [card(464444444, "3D printed tidy", "carol", sold=7)]},
    )


def test_second_scan_skips_ground_already_covered():
    db = Database(":memory:")
    c1 = FakeClient(**_world())
    Crawler(c1, db, Settings(workers=1, request_delay=0, max_sellers=10)).run(["stand"])
    assert {"alice", "bob", "carol"} <= db.known_sellers()
    c2 = FakeClient(**_world())
    Crawler(c2, db, Settings(workers=1, request_delay=0, max_sellers=10)).run(["stand"])
    assert not any(k == "seller" for k, _ in c2.calls)       # every seller already scanned
    assert ("search", "3d printed hook") not in c2.calls      # nor re-walk old searches
    assert ("search", "stand") in c2.calls                    # but the seed you typed still runs
    assert not any(k == "desc" for k, _ in c2.calls)          # known listings aren't re-checked


def test_sold_correction_survives_rescans():
    db = Database(":memory:")
    kw = dict(title="3D printed stand", url="u", price=3.18, seller="d", image_url=None, evidence="title")
    db.upsert_listing(item_id="1", sold=57, **kw)
    db.set_sold("1", 1008)
    p = db.product("1")
    assert p["sold"] == 1008 and p["sold_checked_at"]
    db.upsert_listing(item_id="1", sold=57, **kw)        # a later scan sees the partial card figure again
    assert db.product("1")["sold"] == 1008


def test_failed_seller_is_counted_not_fatal():
    from printscout.scraper.http import FetchError

    class Flaky(FakeClient):
        def seller_listings(self, seller):
            raise FetchError("boom")
    stats = Crawler(Flaky(**_world()), Database(":memory:"),
                    Settings(workers=1, request_delay=0, max_sellers=10)).run(["stand"])
    assert stats.errors >= 2 and stats.searches_run >= 1
