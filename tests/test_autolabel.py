from printscout.db import Database
from printscout.scraper.http import EbayClient


class ImgClient(EbayClient):
    def __init__(self):
        super().__init__(delay=0)
        self.fetched = []

    def image(self, url):
        self.fetched.append(url)
        return b"\xff\xd8fakejpeg"


def seeded_db():
    db = Database(":memory:")
    for i in range(3):
        db.upsert_listing(item_id=f"10000000{i}", title="3D printed hook", url="u", price=4.0, sold=5,
                          seller="alice", image_url=f"https://i.ebayimg.com/p{i}.jpg", evidence="description")
    db.upsert_listing(item_id="100000009", title="hook", url="u", price=4.0, sold=5, seller="bob",
                      image_url="https://i.ebayimg.com/x.jpg", evidence="image")  # model's own guess: excluded
    db.record_rejected("200000000", "Steel hook", "https://i.ebayimg.com/n0.jpg", "carl")
    return db


def test_build_training_set(tmp_data):
    from printscout.vision import autolabel
    db, client = seeded_db(), ImgClient()
    r = autolabel.build(db, client, on_log=lambda m: None)
    assert r == {"positives": 3, "negatives": 1}
    assert autolabel.counts() == {"3d_printed": 3, "not_3d_printed": 1}
    assert "https://i.ebayimg.com/x.jpg" not in client.fetched
    # running again doesn't duplicate
    assert autolabel.build(db, ImgClient(), on_log=lambda m: None)["positives"] == 0


def test_group_of_and_grouped_split():
    import pytest
    pytest.importorskip("torch")
    from printscout.vision.autolabel import auto_filename, group_of
    from printscout.vision.train import split_by_group
    assert group_of(auto_filename("my shop!", "123")) == "seller:my-shop-"
    assert group_of("upload_abc.jpg") == "file:upload_abc.jpg"
    # 5 sellers x 4 photos each: no seller may appear on both sides
    groups = [f"seller:{i // 4}" for i in range(20)]
    tr, va = split_by_group(groups, 0.2, seed=1)
    assert va and tr and not ({groups[i] for i in tr} & {groups[i] for i in va})
    # one giant seller can't be split by group → falls back to per-image split
    tr, va = split_by_group(["seller:a"] * 10, 0.2, seed=1)
    assert len(va) == 2 and len(tr) == 8
