"""HTML parsing for eBay search result and description pages.

eBay serves two result-card layouts: the long-standing ``li.s-item`` markup and
the newer ``li.s-card`` markup. Class names change often, so the parser uses
selectors where they are stable and falls back to regular expressions over a
card's visible text (e.g. "1,234 sold", "seller_name (5,210) 99.8%").
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from bs4 import BeautifulSoup, Tag

# item number from "/itm/123456789012" or "/itm/some-title-slug/123456789012"
ITEM_ID_RE = re.compile(r"/itm/(?:[^/?#]+/)?(\d{9,15})")
# "1,299.99", "4.5" or "12" - the £ sign gets skipped since the match starts at a digit
PRICE_RE = re.compile(r"(\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?)")
# "1,234 sold", "1.2K sold", "10K+ sold"
SOLD_RE = re.compile(r"(\d[\d,.]*\s*[kK]?)\+?\s+sold\b", re.I)
# Legacy: "seller_name (5,210) 99.8%"
SELLER_LEGACY_RE = re.compile(r"([\w.\-*]{2,64})\s+\(([\d,.]+\s*[kK]?)\)\s+[\d.]+%")
# Newer: "seller_name 99.8% positive (5.2K)"
SELLER_NEW_RE = re.compile(r"([\w.\-*]{2,64})\s+[\d.]+%\s+positive\s+\(([\d,.]+\s*[kK]?)\)", re.I)
# "2,345 results" / "10,000+ results" in the header above the listings
RESULT_COUNT_RE = re.compile(r"([\d,]+)\+?\s+results?", re.I)

# screen-reader bits eBay puts inside the title element
TITLE_NOISE = ("New listing", "NEW LISTING", "Opens in a new window or tab")


# one listing card from a search or shop page
@dataclass
class ParsedItem:
    item_id: str
    title: str
    url: str
    price: float | None = None
    sold: int = 0
    seller: str | None = None
    seller_feedback: int | None = None
    image_url: str | None = None


# '1,234' -> 1234, '1.2K' -> 1200, '10K+' -> 10000
def parse_count(text: str) -> int:
    t = text.replace(",", "").replace("+", "").replace(" ", "").lower()
    # eBay shortens big numbers, so "5.2K" needs multiplying back up
    multiply = 1
    if t.endswith("k"):
        multiply = 1000
        t = t[:-1]
    try:
        return round(float(t) * multiply)
    except ValueError:
        return 0


# first price in the text. ranges like '£3.99 to £7.99' give the lower price
def parse_price(text: str | None) -> float | None:
    if not text:
        return None
    m = PRICE_RE.search(text.replace("\xa0", " "))  # \xa0 = non-breaking space, eBay uses them in prices
    return float(m.group(1).replace(",", "")) if m else None


def extract_item_id(url: str) -> str | None:
    m = ITEM_ID_RE.search(url or "")
    return m.group(1) if m else None


# plain listing url with the tracking parameters cut off.
# search links carry a load of ?hash=...&amdata=... junk, and the same item
# would otherwise get saved under lots of different urls
def clean_url(url: str) -> str:
    item_id = extract_item_id(url)
    return f"https://www.ebay.co.uk/itm/{item_id}" if item_id else url


def _clean_title(text: str) -> str:
    for noise in TITLE_NOISE:
        text = text.replace(noise, "")
    return re.sub(r"\s+", " ", text).strip()


def _best_image(card: Tag) -> str | None:
    for img in card.find_all("img"):
        # lazy-loaded images keep the real url in data-src / data-defer-load, src is a placeholder
        for attr in ("src", "data-src", "data-defer-load"):
            src = img.get(attr) or ""
            if "ebayimg.com" in src:
                # s-l140.jpg -> s-l500.jpg, bigger version of the thumbnail (better for the AI model)
                return re.sub(r"s-l\d+\.(jpg|jpeg|png|webp)", r"s-l500.\1", src)
    return None


# all the visible text in a card squashed onto one line, what the regexes run over
def _card_text(card: Tag) -> str:
    return re.sub(r"\s+", " ", card.get_text(" ", strip=True))


# reads one listing card, or None if it isn't a real listing
def _parse_card(card: Tag) -> ParsedItem | None:
    link = card.select_one("a[href*='/itm/']")
    url = link.get("href", "") if link else ""
    # newer s-card layout has the id as an attribute, older one only in the link
    item_id = card.get("data-listingid") or extract_item_id(url)
    if not item_id or not url:
        return None

    # old class, new class, then role=heading in case they rename both again
    title_el = card.select_one(".s-item__title, .s-card__title, [role='heading']")
    title = _clean_title(title_el.get_text(" ", strip=True) if title_el else "")
    if not title or title.lower().startswith("shop on ebay"):
        return None  # eBay placeholder card

    price_el = card.select_one(".s-item__price, .s-card__price")
    text = _card_text(card)

    item = ParsedItem(item_id=str(item_id), title=title, url=clean_url(url),
                      price=parse_price(price_el.get_text(" ", strip=True) if price_el else None),
                      image_url=_best_image(card))

    # sold count isn't in a stable element, so just regex the whole card text.
    # can be lower than the real total, see parse_item_sold
    m = SOLD_RE.search(text)
    if m:
        item.sold = parse_count(m.group(1))

    # old layout has a seller element, new one doesn't so fall back to the card text.
    # try the old format first, then the newer "99.8% positive (5.2K)" one
    seller_el = card.select_one(".s-item__seller-info-text")
    seller_text = seller_el.get_text(" ", strip=True) if seller_el else text
    m = SELLER_LEGACY_RE.search(seller_text) or SELLER_NEW_RE.search(seller_text)
    if m:
        item.seller = m.group(1)
        item.seller_feedback = parse_count(m.group(2))
    return item


# every listing on a search results or shop page
def parse_search_results(html: str) -> list[ParsedItem]:
    soup = BeautifulSoup(html, "lxml")
    items = {}  # item id -> item, so a listing that turns up twice is only kept once
    for card in soup.select("li.s-item, li.s-card, li[data-listingid]"):  # both card layouts
        item = _parse_card(card)
        if item and item.item_id not in items:
            items[item.item_id] = item
    return list(items.values())


# total results for a search, rough measure of how crowded that product is
def parse_result_count(html: str) -> int | None:
    soup = BeautifulSoup(html, "lxml")
    el = soup.select_one(".srp-controls__count-heading, .result-count__count-heading")
    text = el.get_text(" ", strip=True) if el else ""
    m = RESULT_COUNT_RE.search(text)
    return int(m.group(1).replace(",", "")) if m else None


# the visible text of a listing's description, in lower case
def parse_description_text(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()  # otherwise css/js ends up in the text
    return re.sub(r"\s+", " ", soup.get_text(" ", strip=True)).lower()


# whole-word phrase match ignoring case. "3d printed" matches "3D printed", "3d-printed"
# and "3D  Printed", and "prop" doesn't match inside "property"
def phrase_in(text: str, phrase: str) -> bool:
    if not phrase:
        return False
    words = [re.escape(w) for w in phrase.lower().split()]
    # spaces or dashes between the words, and no letter or digit either side of the phrase
    pattern = r"(?<!\w)" + r"[\s\-]*".join(words) + r"(?!\w)"
    return re.search(pattern, text.lower()) is not None


# the "x sold" bit next to the quantity box, which has had a few different class names
_ITEM_SOLD_SELECTORS = ("#qtyAvailability, .x-quantity__availability, .d-quantity__availability, "
                        "[class*='quantity__availability'], [data-testid='x-quantity']")


# total units sold, from a listing's own page ("More than 10 available · 1,008 sold").
# search and shop cards don't always show the full count (listings with colour or
# size options in particular), but the listing page does
def parse_item_sold(html: str) -> int | None:
    soup = BeautifulSoup(html, "lxml")
    for el in soup.select(_ITEM_SOLD_SELECTORS):
        m = SOLD_RE.search(el.get_text(" ", strip=True))
        if m:
            return parse_count(m.group(1))
    text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))
    # Fallback: a "sold" figure right next to the quantity / availability text
    m = re.search(r"available[^.]{0,80}?(\d[\d,.]*\s*[kK]?)\+?\s+sold", text, re.I)
    if m:
        return parse_count(m.group(1))
    return None
