
from __future__ import annotations

import random
import time
from urllib.parse import urlencode

import requests

# looks like a normal browser. the default python-requests one gets blocked fast
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept-Language": "en-GB,en;q=0.9",
}

# rate limited or eBay having a moment, worth another go
RETRY_STATUS = {429, 500, 502, 503, 504}

SEARCH_URL = "https://www.ebay.co.uk/sch/i.html"


class FetchError(RuntimeError):
    pass


class EbayClient:
    def __init__(self, delay: float = 1.0, retries: int = 3, timeout: float = 20.0):
        self.delay = delay      # seconds to wait before each request
        self.retries = retries  # extra tries after the first one fails
        self.timeout = timeout

    # every request goes through here: polite delay, then retry with exponential back-off.
    # returns the page as text, or the raw bytes for an image
    def get(self, url: str, *, binary: bool = False) -> str | bytes:
        error = None
        for attempt in range(self.retries + 1):
            # wait a slightly random time, which looks less robotic
            time.sleep(self.delay * random.uniform(0.6, 1.4))
            try:
                resp = requests.get(url, headers=HEADERS, timeout=self.timeout)
            except requests.RequestException as exc:
                error = exc  # couldn't connect, try again
            else:
                if resp.ok:
                    return resp.content if binary else resp.text
                error = f"HTTP {resp.status_code}"
                if resp.status_code not in RETRY_STATUS:
                    break  # e.g. 404 page not found, retrying won't help
            # wait 1s, 2s, 4s... before the next try
            time.sleep(2 ** attempt)
        raise FetchError(f"GET {url} failed: {error}")

    # eBay pages
    def search(self, term: str) -> str:
        # _nkw = keywords, _ipg = results per page
        return self.get(f"{SEARCH_URL}?{urlencode({'_nkw': term, '_ipg': 60})}")

    def sold_search(self, term: str) -> str:
        # listings that sold (eBay shows roughly the last 90 days)
        params = {"_nkw": term, "_ipg": 60, "LH_Sold": 1, "LH_Complete": 1}
        return self.get(f"{SEARCH_URL}?{urlencode(params)}")

    def seller_listings(self, seller: str) -> str:
        # _ssn filters the search to one seller, so this is basically their shop
        return self.get(f"{SEARCH_URL}?{urlencode({'_ssn': seller, '_ipg': 120})}")

    def description(self, item_id: str) -> str:
        # the description sits in an iframe on the listing page, this is the iframe's own url
        return self.get(f"https://itm.ebaydesc.com/itmdesc/{item_id}")

    def image(self, url: str) -> bytes:
        return self.get(url, binary=True)
