from printscout.config import Settings
from printscout.evaluator import best_per_group, evaluate, rank, search_query_for, unit_costs


def product(**kw):
    base = {"item_id": "1", "title": "Thing", "price": 10.0, "sold": 100, "competition": None,
            "competitor_median": None, "grams": 50, "print_hours": 2, "evidence": "title",
            "status": "new"}
    return {**base, **kw}


def test_unit_costs_maths():
    s = Settings(fee_percent=10, fee_minimum=0, fee_fixed=0.3, filament_cost_per_kg=20, electricity_cost_per_hour=0.05,
                 machine_cost_per_hour=0.1, packaging_cost=0.5, postage_cost=0)
    c = unit_costs(10.0, 50, 2, s)
    # fees 1.30, filament 1.00, electricity 0.10, machine 0.20, packaging 0.50 => 3.10
    assert c.profit == 6.90
    assert round(c.margin, 3) == 0.69
    assert c.profit_per_hour == 3.45


def test_more_sales_scores_higher():
    s = Settings()
    assert evaluate(product(sold=1000), s).score > evaluate(product(sold=10), s).score


def test_loss_making_product_is_capped():
    ev = evaluate(product(price=2.0, sold=5000, grams=300, print_hours=10), Settings())
    assert ev.costs.profit < 0
    assert ev.score <= 25 and ev.verdict == "Avoid"
    assert any("Loses money" in f for f in ev.flags)


def test_missing_data_is_flagged_not_crashing():
    ev = evaluate(product(price=None, grams=None, print_hours=None), Settings())
    assert ev.costs is None
    assert any("No price" in f for f in ev.flags)
    assert any("Market not checked" in f for f in ev.flags)


def test_more_competition_is_a_proven_market():
    s = Settings()
    assert evaluate(product(competition=3000), s).score > evaluate(product(competition=20), s).score


def test_rank_sorts_best_first():
    ranked = rank([product(item_id="a", sold=1), product(item_id="b", sold=900)], Settings())
    assert [p["item_id"] for p in ranked] == ["b", "a"]


def test_search_query_strips_noise():
    q = search_query_for("NEW 3D Printed BT Smart Hub 2 Wall Mount Bracket - Black - UK Free P&P")
    assert q == "bt smart hub 2 wall mount"  # keeps model numbers, drops noise


def test_weights_change_ranking():
    busy_seller = product(item_id="a", sold=900, price=5.0)    # sells loads, thin margin
    high_margin = product(item_id="b", sold=20, price=25.0)    # sells little, big margin
    sales_first = Settings(weight_demand=90, weight_market=0, weight_competition=0,
                           weight_margin=5, weight_efficiency=5)
    profit_first = Settings(weight_demand=5, weight_market=0, weight_competition=0,
                            weight_margin=60, weight_efficiency=35)
    assert rank([busy_seller, high_margin], sales_first)[0]["item_id"] == "a"
    assert rank([busy_seller, high_margin], profit_first)[0]["item_id"] == "b"


def test_weights_are_normalised():
    from printscout.config import WEIGHT_FIELDS
    s = Settings(**{k: 2 for k in WEIGHT_FIELDS})
    assert all(abs(v - 1 / len(WEIGHT_FIELDS)) < 1e-9 for v in s.weights().values())
    zero = Settings.from_dict({k: 0 for k in WEIGHT_FIELDS})
    assert sum(zero.weights().values()) == 0  # all zero doesn't crash with a divide by zero


def test_ip_risk_flags_and_penalty():
    s = Settings()
    plain = evaluate(product(title="Toothbrush stand"), s)
    franchise = evaluate(product(title="Baby Yoda Toothpaste Topper - Star Wars"), s)
    assert franchise.ip_risk == "high" and franchise.score < plain.score
    assert evaluate(product(title="Key ring holder"), s).ip_risk is None  # 'ring' alone isn't a brand


def test_market_boosts_score():
    s = Settings()
    assert evaluate(product(sold=50, market_sold=400, competition=100), s).score > \
        evaluate(product(sold=50, market_sold=3, competition=100), s).score


def test_group_similar_titles():
    from printscout.evaluator import group_similar
    rows = [product(item_id="1", title="Ford Focus Door Seal Fix pack of 4 2011-2019", seller="a", sold=10),
            product(item_id="2", title="Ford Focus Door Seal Repair pack of 4 2011-2019", seller="b", sold=5),
            product(item_id="3", title="Hive thermostat table stand", seller="c", sold=7)]
    group_similar(rows)
    assert rows[0]["group_id"] == rows[1]["group_id"] != rows[2]["group_id"]
    assert rows[0]["group_size"] == 2 and rows[0]["group_sold"] == 15
    assert len(best_per_group(rank(rows, Settings()))) == 2


def test_accessory_for_franchise_is_lower_risk():
    from printscout.evaluator import ip_risk
    assert ip_risk("Wall Mount Display for Lego 75367 Venator")[0] == "brand"
    assert ip_risk("Giant Spider Man LEGO Brick Man 16 inch")[0] == "high"


def test_amazon_fees_and_minimum():
    s = Settings(fee_percent=15, fee_minimum=0.25, fee_fixed=0)
    assert unit_costs(10.0, 0, 0, s).fees == 1.50
    assert unit_costs(1.0, 0, 0, s).fees == 0.25  # minimum referral fee applies
    indiv = Settings(fee_percent=15, fee_minimum=0.25, fee_fixed=0.75)
    assert unit_costs(10.0, 0, 0, indiv).fees == 2.25


def test_your_sell_price_overrides_ebay_price():
    s = Settings()
    ebay_only = evaluate(product(price=5.0), s)
    mine = evaluate(product(price=5.0, sell_price=12.0), s)
    assert mine.costs.price == 12.0 and mine.costs.profit > ebay_only.costs.profit
