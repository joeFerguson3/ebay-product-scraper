from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass

from ..config import Settings
from ..db import Database
from .http import EbayClient, FetchError
from .parsers import ParsedItem, parse_description_text, parse_search_results, phrase_in


@dataclass
class CrawlStats:
    searches_run: int = 0
    sellers_scanned: int = 0
    new_listings: int = 0
    errors: int = 0



@dataclass
class Verdict:
    is_printed: bool
    evidence: str



def normalise_term(term: str) -> str:
    return " ".join(term.lower().split())


class Crawler:
    def __init__(self, client: EbayClient, db: Database, settings: Settings, *,
                 detector=None, stop_event: threading.Event | None = None,
                 on_log=print, on_progress=lambda p: None):
        self.client = client
        self.db = db
        self.s = settings
        # the image model, only used if it's switched on in settings
        self.detector = detector if settings.use_image_detector else None
        self.stop_event = stop_event or threading.Event()
        self._log = on_log
        self._progress = on_progress

        self.stats = CrawlStats()
        # one lock for everything below, simpler than juggling several
        self._lock = threading.Lock()
        self._searches = deque()  # search terms waiting to be run
        self._sellers = deque()   # seller names waiting to be scanned
        # sellers and searches from earlier scans are skipped so each scan explores new ground
        self._seen_terms = db.known_search_terms()
        self._seen_sellers = db.known_sellers()
        self._verdicts = {}
        self._sellers_started = 0  # counts against max_sellers
        self._active = 0  # tasks currently running, so idle workers know whether more work might turn up

    def run(self, seeds: list[str]) -> CrawlStats:
        # the seeds always run, even if an earlier scan searched them
        for seed in seeds:
            self._searches.append(seed)
            self._seen_terms.add(normalise_term(seed))
        self._log(f"Starting scan with {len(self._searches)} searches, {self.s.workers} workers "
                  f"and a budget of {self.s.max_sellers} sellers. {len(self._seen_sellers)} sellers "
                  f"from earlier scans will be skipped.")
        # workers pull from the shared queues until the budget runs out or stop is pressed
        threads = []
        for _ in range(self.s.workers):
            t = threading.Thread(target=self._worker, daemon=True)
            t.start()
            threads.append(t)
        for t in threads:
            t.join()  # wait for every worker to finish
        st = self.stats
        self._log(("Scan stopped" if self.stop_event.is_set() else "Scan finished")
                  + f". Scanned {st.sellers_scanned} sellers, ran {st.searches_run} searches "
                    f"and found {st.new_listings} new products.")
        return self.stats

    # the next thing for a worker to do: ("seller", name), ("search", term) or None
    def _next_task(self):
        # sellers first - they're where the actual listings get saved.
        # searches only feed more sellers, so no point running them once the budget's gone
        with self._lock:
            if self._sellers_started >= self.s.max_sellers:
                return None
            if self._sellers:
                self._sellers_started += 1
                self._active += 1
                return ("seller", self._sellers.popleft())
            if self._searches:
                self._active += 1
                return ("search", self._searches.popleft())
            return None

    def _finished(self) -> bool:
        # empty queues alone isn't enough, a running task might still add more
        with self._lock:
            budget_used = self._sellers_started >= self.s.max_sellers
            nothing_left = not self._sellers and not self._searches
            return self._active == 0 and (budget_used or nothing_left)

    # each worker thread runs this loop
    def _worker(self) -> None:
        while not self.stop_event.is_set():
            task = self._next_task()
            if task is None:
                if self._finished():
                    return
                time.sleep(0.2)  # nothing queued right now, but another worker may add some
                continue
            kind, key = task  # key is the seller name or the search term
            try:
                if kind == "seller":
                    self._scan_seller(key)
                else:
                    self._run_search(key)
            except Exception as exc:  # never let one bad page kill a worker
                with self._lock:
                    self.stats.errors += 1
                self._log(f"The {kind} {key} failed: {exc}")
            with self._lock:
                self._active -= 1
                # progress = how much of the seller budget has been used
                progress = min(1.0, self._sellers_started / self.s.max_sellers)
            self._progress(progress)

    # is it 3D printed? the answer is remembered because checking can be slow
    def classify(self, item: ParsedItem) -> Verdict:
        if item.item_id not in self._verdicts:
            self._verdicts[item.item_id] = self._check(item)
        return self._verdicts[item.item_id]

    def _check(self, item: ParsedItem) -> Verdict:
        # decided by an earlier scan - no need to read the description again
        known = self.db.known_verdict(item.item_id)
        if known is not None:
            printed, evidence = known
            return Verdict(printed, evidence if printed else "known")

        # cheapest checks first: title (free), then description (1 request), then image model
        title = item.title.lower()
        if any(phrase_in(title, p) for p in self.s.excluded_phrases):
            return Verdict(False, "excluded")
        if phrase_in(title, self.s.required_phrase):
            return Verdict(True, "title")

        # lots of sellers only say "3D printed" in the description, not the title,
        # so without this a load of real ones get missed
        text = ""
        if self.s.check_descriptions:
            try:
                text = parse_description_text(self.client.description(item.item_id))
            except FetchError:
                pass
            if any(phrase_in(text, p) for p in self.s.excluded_phrases):
                return Verdict(False, "excluded")
            if phrase_in(text, self.s.required_phrase):
                return Verdict(True, "description")

        # last resort, ask the CNN about the photo
        if self.detector and item.image_url:
            try:
                prob = self.detector.predict_bytes(self.client.image(item.image_url))
                return Verdict(prob >= self.s.detector_threshold, "image")
            except Exception as exc:  # image download or model failure
                self._log(f"Image check failed for listing {item.item_id}: {exc}")

        # "no mention" = the description was read and never says 3D printed.
        # "unknown" = the description wasn't read, so it's only a guess
        return Verdict(False, "no mention" if text else "unknown")

    # search -> find decent sellers selling 3D printed stuff -> queue them
    def _run_search(self, term: str) -> None:
        items = parse_search_results(self.client.search(term))
        found = 0
        for item in items:
            if self.stop_event.is_set():
                break
            # low feedback sellers are usually new or tiny, not worth following
            if not item.seller or (item.seller_feedback or 0) < self.s.min_seller_feedback:
                continue
            if item.seller in self._seen_sellers:
                continue  # skip before classify() so no wasted description fetch
            verdict = self.classify(item)
            if not verdict.is_printed:
                self._remember_rejected(item, verdict)
                continue
            with self._lock:
                if item.seller not in self._seen_sellers:
                    self._seen_sellers.add(item.seller)
                    self._sellers.append(item.seller)
                    found += 1
        self.db.record_search(term)
        with self._lock:
            self.stats.searches_run += 1
        self._log(f"Searched for {term[:70]} and got {len(items)} results and {found} new sellers.")

    # go through one seller's shop, save their 3D printed listings and turn each title into a new search.
    # this is the bit that makes it snowball
    def _scan_seller(self, seller: str) -> None:
        items = parse_search_results(self.client.seller_listings(seller))
        self.db.record_seller(seller)
        matched = new = 0
        for item in items:
            if self.stop_event.is_set():
                break
            verdict = self.classify(item)
            if not verdict.is_printed:
                self._remember_rejected(item, verdict, seller)
                continue
            matched += 1
            is_new = self.db.upsert_listing(item_id=item.item_id, title=item.title, url=item.url,
                                            price=item.price, sold=item.sold, seller=seller,
                                            image_url=item.image_url, evidence=verdict.evidence)
            if is_new:
                new += 1
            # the title becomes a new search, unless it's been searched before
            with self._lock:
                if normalise_term(item.title) not in self._seen_terms:
                    self._seen_terms.add(normalise_term(item.title))
                    self._searches.append(item.title)
        with self._lock:
            self.stats.sellers_scanned += 1
            self.stats.new_listings += new
        self._log(f"Scanned {seller}. {matched} of their {len(items)} listings are 3D printed, {new} of them new.")

    # saves probably-not-3D-printed listings as negative training examples.
    # only ones whose description was read and never mentions 3D printing.
    # excluded-phrase rejects aren't used (a '3D printed prop' is still 3D printed)
    def _remember_rejected(self, item: ParsedItem, verdict: Verdict, seller: str | None = None) -> None:
        if verdict.evidence == "no mention" and item.image_url:
            self.db.record_rejected(item.item_id, item.title, item.image_url, seller or item.seller)
