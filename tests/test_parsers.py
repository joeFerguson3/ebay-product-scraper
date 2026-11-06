from printscout.scraper.parsers import (clean_url, extract_item_id, parse_count, parse_description_text,
                                        parse_price, parse_result_count, parse_search_results, phrase_in)


def test_parse_count():
    assert parse_count("1,234") == 1234
    assert parse_count("2.1K") == 2100
    assert parse_count("10K+") == 10000
    assert parse_count("abc") == 0


def test_parse_price():
    assert parse_price("£7.99") == 7.99
    assert parse_price("£4.50 to £6.00") == 4.50
    assert parse_price("£1,299.00") == 1299.0
    assert parse_price(None) is None


def test_item_id_and_clean_url():
    url = "https://www.ebay.co.uk/itm/126397249428?hash=item1d6d"
    assert extract_item_id(url) == "126397249428"
    assert extract_item_id("https://www.ebay.co.uk/itm/some-title/126397249428") == "126397249428"
    assert clean_url(url) == "https://www.ebay.co.uk/itm/126397249428"


def test_legacy_layout(fixture_html):
    items = parse_search_results(fixture_html("search_legacy.html"))
    assert [i.item_id for i in items] == ["126397249428", "235459565274"]  # placeholder skipped
    a, b = items
    assert a.title == "BT Smart Hub 2 Wall Mount Bracket - 3D Printed"
    assert a.price == 7.99 and a.sold == 1265
    assert (a.seller, a.seller_feedback) == ("printmaster_uk", 5210)
    assert a.image_url.endswith("s-l500.jpg")
    assert a.url == "https://www.ebay.co.uk/itm/126397249428"
    assert b.sold == 0 and b.price == 4.5 and b.seller_feedback == 42
    assert parse_result_count(fixture_html("search_legacy.html")) == 1482


def test_new_card_layout(fixture_html):
    (item,) = parse_search_results(fixture_html("search_new.html"))
    assert item.item_id == "127118853761"
    assert item.title == "IKEA STYRBAR Remote Adapter UK Light Switch Cover"
    assert item.price == 9.49 and item.sold == 2100
    assert (item.seller, item.seller_feedback) == ("makerbench", 1200)
    assert parse_result_count(fixture_html("search_new.html")) == 2300


def test_description_and_phrase(fixture_html):
    text = parse_description_text(fixture_html("description.html"))
    assert "var a" not in text
    assert phrase_in(text, "3d printed")  # matches '3D-Printed'
    assert not phrase_in("a lovely property", "prop")  # whole words only
    assert phrase_in("movie prop replica", "prop")


def test_parse_item_sold_from_listing_page():
    from printscout.scraper.parsers import parse_item_sold
    page = ('<div class="x-quantity__availability"><span>More than 10 available</span>'
            '<span> · </span><span>1,008 sold</span></div><div>Similar items: 57 sold</div>')
    assert parse_item_sold(page) == 1008
    legacy = '<span id="qtyAvailability"><span class="ux-textspans">Last one</span><span class="ux-textspans">2.1K sold</span></span>'
    assert parse_item_sold(legacy) == 2100
    fallback = "<p>Quantity: 3 available / 45 sold</p>"
    assert parse_item_sold(fallback) == 45
    assert parse_item_sold("<p>No sales info</p>") is None
