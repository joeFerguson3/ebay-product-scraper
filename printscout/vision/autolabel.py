
from __future__ import annotations

import re
from pathlib import Path

from ..config import CLASS_NEGATIVE, CLASS_POSITIVE, DATASET_DIR
from ..db import Database
from ..scraper.http import EbayClient, FetchError

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
# characters that aren't allowed in a filename, e.g. the ' and space in "joe's prints"
_UNSAFE = re.compile(r"[^A-Za-z0-9_.-]+")


# e.g. joes-prints__123456789.jpg. the seller is kept in the name so training
# can keep all of one seller's photos together
def photo_filename(seller: str | None, item_id: str) -> str:
    seller = _UNSAFE.sub("-", seller or "unknown")
    return f"{seller}__{item_id}.jpg"


# the seller from a photo's filename (the part before the item id).
# older photos are called auto__seller__id.jpg, which works too
def seller_of(filename: str) -> str:
    parts = Path(filename).name.split("__")
    return parts[-2] if len(parts) > 1 else parts[0]


# the image files in a folder
def _images(folder: Path) -> list[Path]:
    if not folder.exists():
        return []
    return [p for p in folder.iterdir() if p.suffix.lower() in IMAGE_EXTS]


# numbers for the detector page
def counts() -> dict:
    return {CLASS_POSITIVE: len(_images(DATASET_DIR / CLASS_POSITIVE)),
            CLASS_NEGATIVE: len(_images(DATASET_DIR / CLASS_NEGATIVE))}


# item ids already in the training set, so the same photo isn't added twice
def _existing_ids() -> set[str]:
    ids = set()
    for label in (CLASS_POSITIVE, CLASS_NEGATIVE):
        for p in _images(DATASET_DIR / label):
            ids.add(p.stem.split("__")[-1])  # the item id is the last part of seller__id
    return ids


# downloads listing photos from the database into the two training folders
def build(db: Database, client: EbayClient, *, max_per_class: int = 300,
          on_log=print, on_progress=lambda p: None, stop_event=None) -> dict:
    have = _existing_ids()
    # positives: saved listings where the title/description actually said 3D printed.
    # image-only matches left out, otherwise the model would be training on its own guesses
    positive_rows = db.query("""SELECT item_id, seller, image_url FROM listings
                               WHERE image_url IS NOT NULL AND evidence IN ('title', 'description')
                                 AND status != 'dismissed' ORDER BY RANDOM()""")
    # negatives: what the crawler put in rejected_listings (read the description, no mention of printing)
    negative_rows = db.query("""SELECT item_id, seller, image_url FROM rejected_listings
                               WHERE image_url IS NOT NULL ORDER BY RANDOM()""")
    positives = [r for r in positive_rows if r["item_id"] not in have][:max_per_class]
    negatives = [r for r in negative_rows if r["item_id"] not in have][:max_per_class]

    on_log(f"Adding {len(positives)} 3D printed and {len(negatives)} not 3D printed photos")
    if not negatives:
        on_log("No negative examples yet. Run a scan with Check listing descriptions turned on.")

    # download each photo into its class folder
    to_download = [(r, CLASS_POSITIVE) for r in positives] + [(r, CLASS_NEGATIVE) for r in negatives]
    saved = {CLASS_POSITIVE: 0, CLASS_NEGATIVE: 0}
    for i, (row, label) in enumerate(to_download):
        if stop_event and stop_event.is_set():
            break
        path = DATASET_DIR / label / photo_filename(row["seller"], row["item_id"])
        try:
            data = client.image(row["image_url"])
        except FetchError:
            continue  # listing probably ended and the image is gone
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        saved[label] += 1
        on_progress((i + 1) / len(to_download))
    on_log(f"Saved {saved[CLASS_POSITIVE]} printed and {saved[CLASS_NEGATIVE]} not printed photos")
    return {"positives": saved[CLASS_POSITIVE], "negatives": saved[CLASS_NEGATIVE]}

