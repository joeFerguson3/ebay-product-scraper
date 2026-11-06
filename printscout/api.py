"""FastAPI backend serving the REST API and the single-page dashboard."""
from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import quote_plus

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__
from .config import DB_PATH, METRICS_PATH, MODEL_PATH, Settings, ensure_dirs, load_settings, save_settings
from .db import STATUSES, Database
from .evaluator import best_per_group, check_market, evaluate, rank, search_query_for
from .jobs import Job, JobManager
from .scraper import Crawler, EbayClient, FetchError
from .vision import autolabel, torch_available

WEB_DIR = Path(__file__).parent / "web"


# what the scan button sends
class ScanRequest(BaseModel):
    seeds: list[str] = []
    max_sellers: int | None = None


# uses the default grams/hours from settings
# slicer estimates and your selling price from the product panel
class CostsRequest(BaseModel):
    grams: float | None = Field(default=None, ge=0)
    print_hours: float | None = Field(default=None, ge=0)
    sell_price: float | None = Field(default=None, ge=0)


# shortlist / dismiss / back to new
class StatusRequest(BaseModel):
    status: str


# options for training the image model
class TrainRequest(BaseModel):
    epochs: int = Field(default=12, ge=1, le=100)
    # Starting model
    arch: str = "mobilenet_v3_small"


# build a training set from photos the scans already found
class AutoBuildRequest(BaseModel):
    max_per_class: int = Field(default=300, ge=1, le=5000)


# sets up the whole app
def create_app(db_path: Path | str = DB_PATH) -> FastAPI:
    ensure_dirs()
    app = FastAPI(title="PrintScout", version=__version__)
    db = Database(db_path)
    jobs = JobManager()
    app.state.db, app.state.jobs = db, jobs

    # read from disk every time so changes on the Settings page apply straight away
    def settings():
        return load_settings()

    # the last ranking, so pages don't have to score every product again on each request
    rank_cache = {"made_from": None, "rows": []}

    # ranks product using current settings when cache is changed, or returns cache otherwise
    def ranked_all(s: Settings | None = None) -> list[dict]:

        s = s or settings()

        current = (db.version, json.dumps(s.to_dict(), sort_keys=True))
        if rank_cache["made_from"] != current:
            rank_cache["rows"] = rank(db.products(), s)
            rank_cache["made_from"] = current
        return rank_cache["rows"]

    # returns the best new finds, no shortlisted products
    def new_finds(rows: list[dict]) -> list[dict]:
        taken = {r["group_id"] for r in rows if r["status"] == "shortlisted"}
        return best_per_group([r for r in rows if r["status"] == "new" and r["group_id"] not in taken])

    # 409 back to the UI if that kind of job is already going
    # starts a background job and returns its info
    def start_job(kind: str, fn) -> dict:
        try:
            return jobs.start(kind, fn).to_dict()
        except RuntimeError as exc:
            raise HTTPException(409, str(exc))

    # looks up a product
    def _product_or_404(item_id: str) -> dict:
        p = db.product(item_id)
        if not p:
            raise HTTPException(404, "product not found")
        return p


    @app.get("/api/stats")
    def stats():
        new = new_finds(ranked_all())
        return {**db.stats(), "new": len(new), "top": new[:5]}

    # returns the saved settings
    @app.get("/api/settings")
    def get_settings():
        return settings().to_dict()

    # saves changes from the Settings page
    @app.put("/api/settings")
    def put_settings(body: dict):
        try:
            s = Settings.from_dict({**settings().to_dict(), **body})
        except (TypeError, ValueError) as exc:
            raise HTTPException(422, str(exc))
        save_settings(s)
        return s.to_dict()


    # every recent job and how far along it is
    @app.get("/api/jobs")
    def list_jobs():
        return [j.to_dict() for j in list(jobs.all.values())]

    # polled by the progress panel while a job run
    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str):
        job = jobs.all.get(job_id)
        if not job:
            raise HTTPException(404, "job not found")
        return job.to_dict(with_logs=True)

    # stop button for a running job
    @app.post("/api/jobs/{job_id}/stop")
    def stop_job(job_id: str):
        return {"stopped": jobs.stop(job_id)}


    # starts a scan in the background
    @app.post("/api/scan")
    def scan(req: ScanRequest):
        s = settings()
        
        if req.max_sellers:
            s.max_sellers = req.max_sellers
        seeds = [x.strip() for x in req.seeds if x.strip()]
        if not seeds:
            raise HTTPException(422, "Add at least one seed search term")
        detector = None
        if s.use_image_detector:
            if not (torch_available() and MODEL_PATH.exists()):
                raise HTTPException(400, "Image detector is enabled but no trained model exists")
            from .vision.predict import get_detector
            detector = get_detector()

        # runs in the background
        def run(job: Job):
            db.start_scan(seeds)
            Crawler(EbayClient(delay=s.request_delay), db, s, detector=detector,
                    stop_event=job.stop_event, on_log=job.log, on_progress=job.set_progress).run(seeds)

        return start_job("scan", run)

    # get product
    @app.get("/api/products")
    def products(status: str = "new", q: str = ""):
        ranked = ranked_all()
        if status == "new":
            ranked = new_finds(ranked)
        elif status in STATUSES:
            ranked = [r for r in ranked if r["status"] == status]
        if q:
            ql = q.lower()
            ranked = [r for r in ranked if ql in r["title"].lower() or ql in (r["seller"] or "").lower()]
        return ranked[:500]

    # everything needed for the product panel
    @app.get("/api/products/{item_id}")
    def product(item_id: str):
        p = _product_or_404(item_id)
        s = settings()
        ranked = ranked_all(s)
        # get similar product
        group_id = next((r["group_id"] for r in ranked if r["item_id"] == item_id), None)
        mates = [{k: r[k] for k in ("item_id", "title", "price", "sold", "seller", "url")}
                 for r in ranked if r["group_id"] == group_id and r["item_id"] != item_id][:10]
      
        p["amazon_search_url"] = "https://www.amazon.co.uk/s?k=" + quote_plus(search_query_for(p["title"]))
        return {"product": p, "evaluation": evaluate(p, s).to_dict(), "similar": mates}

    # updates product information: price printtime weight
    @app.put("/api/products/{item_id}/costs")
    def product_costs(item_id: str, body: CostsRequest):
        _product_or_404(item_id)
        db.save_evaluation(item_id, **body.model_dump())
        return product(item_id)

    # shortlist/dismiss button
    @app.put("/api/products/{item_id}/status")
    def product_status(item_id: str, body: StatusRequest):
        _product_or_404(item_id)
        try:
            db.set_listing_status(item_id, body.status)
        except ValueError as exc:
            raise HTTPException(422, str(exc))
        return {"ok": True}

    # Check market button for one product
    @app.post("/api/products/{item_id}/competition")
    def product_competition(item_id: str):
       
        _product_or_404(item_id)
        s = settings()
        try:
            check_market(EbayClient(delay=0.5), db, item_id, s)
        except FetchError as exc:
            raise HTTPException(502, f"eBay request failed: {exc}")
        return product(item_id)


    # check top new products for competition
    @app.post("/api/competition/batch")
    def batch_competition():
        s = settings()
        targets = [p["item_id"] for p in new_finds(ranked_all(s)) if p["market_sold"] is None][:20]
        if not targets:
            raise HTTPException(400, "All the top new finds are already checked")

        # checks each product one at a time
        def run(job: Job):
            c = EbayClient(delay=s.request_delay)
            for i, item_id in enumerate(targets):
                if job.stop_event.is_set():
                    break
                try:
                    r = check_market(c, db, item_id, s)
                    job.log(f"Searched eBay for {r['competition_query']}. {r['competition']:,} listed "
                            f"and {r['market_sold']:,} sold in the last 90 days.")
                except FetchError as exc:  # continue if one product fails
                    job.log(f"eBay request failed: {exc}")
                job.set_progress((i + 1) / len(targets))
        return start_job("competition", run)

    # get ai detector stats
    @app.get("/api/detector")
    def detector_status():
 
        metrics = json.loads(METRICS_PATH.read_text()) if METRICS_PATH.exists() else None
        return {"torch": torch_available(), "metrics": metrics, "dataset": autolabel.counts(),
                "sources": db.training_sources()}

    # build training set from the scans
    @app.post("/api/detector/auto-build")
    def detector_auto_build(req: AutoBuildRequest):

        def run(job: Job):
            autolabel.build(db, EbayClient(delay=0.3), max_per_class=req.max_per_class,
                            on_log=job.log, on_progress=job.set_progress, stop_event=job.stop_event)
        return start_job("autobuild", run)

    # trains the image model
    @app.post("/api/detector/train")
    def detector_train(req: TrainRequest):
        if not torch_available():
            raise HTTPException(400, "PyTorch is not installed")

        def run(job: Job):
            from .vision.train import train
            train(arch=req.arch, epochs=req.epochs, on_log=job.log,
                         on_progress=job.set_progress, stop_event=job.stop_event)
        return start_job("train", run)

    # test an uploaded image
    @app.post("/api/detector/predict")
    def detector_predict(file: UploadFile = File(...)):
        if not torch_available():
            raise HTTPException(400, "PyTorch is not installed")
        from .vision.predict import get_detector
        det = get_detector()
        if det is None:
            raise HTTPException(400, "No trained model")
        try:
            prob = det.predict_bytes(file.file.read())
        except Exception as exc:
            raise HTTPException(422, f"Could not read image: {exc}")
        s = settings()
        return {"probability": prob, "is_3d_printed": prob >= s.detector_threshold,
                "threshold": s.detector_threshold}


    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")

    @app.get("/")
    def index():
        return FileResponse(WEB_DIR / "index.html")

    return app
