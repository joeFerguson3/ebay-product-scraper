import io
import time

import pytest
from fastapi.testclient import TestClient
from PIL import Image


@pytest.fixture
def client(tmp_data):
    from printscout.api import create_app
    app = create_app(tmp_data / "test.db")
    db = app.state.db
    db.upsert_listing(item_id="111111111", title="3D printed hub mount", url="https://www.ebay.co.uk/itm/111111111",
                      price=8.0, sold=500, seller="alice", image_url=None, evidence="title")
    db.upsert_listing(item_id="222222222", title="3D printed hook", url="https://www.ebay.co.uk/itm/222222222",
                      price=3.0, sold=5, seller="bob", image_url=None, evidence="description")
    return TestClient(app)


def test_index_and_static(client):
    assert "PrintScout" in client.get("/").text
    assert client.get("/static/app.js").status_code == 200


def test_stats_and_products_ranked(client):
    s = client.get("/api/stats").json()
    assert s["listings"] == 2 and s["new"] == 2
    rows = client.get("/api/products").json()
    assert rows[0]["item_id"] == "111111111" and rows[0]["score"] >= rows[1]["score"]
    assert client.get("/api/products?q=hook").json()[0]["item_id"] == "222222222"


def test_costs_update_changes_score(client):
    before = client.get("/api/products/111111111").json()["evaluation"]
    after = client.put("/api/products/111111111/costs", json={"grams": 400, "print_hours": 12}).json()["evaluation"]
    assert after["costs"]["profit"] < before["costs"]["profit"]
    assert after["score"] < before["score"]


def test_status_and_dismiss(client):
    assert client.put("/api/products/222222222/status", json={"status": "dismissed"}).status_code == 200
    assert [r["item_id"] for r in client.get("/api/products").json()] == ["111111111"]
    assert [r["item_id"] for r in client.get("/api/products?status=dismissed").json()] == ["222222222"]
    assert client.put("/api/products/222222222/status", json={"status": "bogus"}).status_code == 422
    assert client.get("/api/products/999").status_code == 404


def test_settings_roundtrip(client):
    s = client.get("/api/settings").json()
    s["fee_percent"] = 5
    s["excluded_phrases"] = ["Laser", " prop "]
    saved = client.put("/api/settings", json=s).json()
    assert saved["fee_percent"] == 5 and saved["excluded_phrases"] == ["laser", "prop"]
    assert client.get("/api/settings").json()["fee_percent"] == 5


def png_bytes():
    buf = io.BytesIO()
    Image.new("RGB", (32, 32), (200, 10, 10)).save(buf, "PNG")
    return buf.getvalue()


def test_predict_without_model(client):
    r = client.post("/api/detector/predict", files={"file": ("a.png", png_bytes(), "image/png")})
    assert r.status_code == 400


def test_scan_job_runs_and_reports(client, monkeypatch):
    import printscout.api as api

    class FakeClient(api.EbayClient):
        def search(self, term):
            return ('<li class="s-item"><a href="https://www.ebay.co.uk/itm/333333333">'
                    '<div class="s-item__title">3D printed clip</div></a><span class="s-item__price">£4</span>'
                    '<span class="s-item__seller-info-text">carol (900) 99%</span></li>')

        def seller_listings(self, seller):
            return self.search("") .replace("333333333", "444444444")

    monkeypatch.setattr(api, "EbayClient", FakeClient)
    job = client.post("/api/scan", json={"seeds": ["clip"], "max_sellers": 3}).json()
    assert client.post("/api/scan", json={"seeds": ["x"]}).status_code in (200, 409)
    for _ in range(50):
        j = client.get(f"/api/jobs/{job['id']}").json()
        if j["status"] != "running":
            break
        time.sleep(0.1)
    assert j["status"] == "done", j
    assert "Scanned 1 sellers" in j["message"]
    assert client.get("/api/stats").json()["scans"] == 1
    assert "444444444" in {r["item_id"] for r in client.get("/api/products").json()}
    assert client.post("/api/scan", json={"seeds": []}).status_code == 422


def test_weight_settings_affect_products(client):
    s = client.get("/api/settings").json()
    detail = client.get("/api/products/111111111").json()
    assert len(detail["evaluation"]["factors"]) == 5 and "similar" in detail
    s.update(weight_demand=100, weight_market=0, weight_competition=0, weight_margin=0, weight_efficiency=0)
    client.put("/api/settings", json=s)
    f = {x["key"]: x["weight"] for x in client.get("/api/products/111111111").json()["evaluation"]["factors"]}
    assert f["demand"] == 1.0 and f["margin"] == 0.0


def test_auto_build_endpoint(client, monkeypatch):
    import printscout.api as api

    class C(api.EbayClient):
        def image(self, url):
            return b"img"

    monkeypatch.setattr(api, "EbayClient", C)
    client.app.state.db.execute("UPDATE listings SET image_url = 'https://i.ebayimg.com/a.jpg'")
    assert client.get("/api/detector").json()["sources"]["positives"] == 2
    job = client.post("/api/detector/auto-build", json={"max_per_class": 10}).json()
    for _ in range(50):
        j = client.get(f"/api/jobs/{job['id']}").json()
        if j["status"] != "running":
            break
        time.sleep(0.1)
    assert j["status"] == "done"
    assert client.get("/api/detector").json()["dataset"]["3d_printed"] == 2


def test_hide_listings_like_picked_products(client):
    db = client.app.state.db
    db.upsert_listing(item_id="333333333", title="3D printed hub mount", url="https://www.ebay.co.uk/itm/333333333",
                      price=8.5, sold=40, seller="carol", image_url=None, evidence="title")
    client.put("/api/products/111111111/status", json={"status": "shortlisted"})
    shown = [r["item_id"] for r in client.get("/api/products?status=new").json()]
    assert "333333333" not in shown and "222222222" in shown
    assert client.get("/api/stats").json()["new"] == 1
