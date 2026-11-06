# PrintScout

**Find profitable products to 3D print, by researching what already sells on eBay.**

PrintScout is a local web app I built for my own 3D printing business. I sell
prints on Amazon UK, and the hardest part is deciding what to make. Instead of
guessing, PrintScout finds 3D-printed products that already have proven sales
on eBay and works out whether each one would be worth making.

![Python](https://img.shields.io/badge/Python-3.10+-3776AB) ![FastAPI](https://img.shields.io/badge/FastAPI-backend-009688) ![PyTorch](https://img.shields.io/badge/PyTorch-CNN-EE4C2C) ![SQLite](https://img.shields.io/badge/SQLite-storage-003B57)

## What it does

- **Finds products.** Start from a few search terms and PrintScout scans eBay
  for sellers of 3D-printed items, then uses their listings to find more
  products and more sellers.
- **Scores each product out of 100.** It weighs how many have sold, how the
  wider market is doing, how many other listings there are, and how much
  profit you'd make per sale and per hour of printing, using your own costs.
  Every score comes with a plain-English explanation.
- **Checks the market.** One click looks up current and recently sold eBay
  listings for a product.
- **Flags risks.** Products based on licensed characters or franchises are
  flagged, and products that would lose money are marked to avoid.
- **Spots 3D printing in photos.** An optional image model (a CNN trained on
  photos from your own scans) recognises 3D-printed items even when the
  listing never says so.

## How you use it

1. **Find products:** enter a few seed searches and start a scan.
2. **Review new finds:** the best-scoring products are at the top. Check the
   market, then shortlist or dismiss them.
3. **Work on your shortlist:** add your slicer estimates and selling price to
   see the real profit.

## Getting started

```bash
git clone https://github.com/joeFerguson3/printscout.git
cd printscout
python -m venv .venv
.venv\Scripts\activate             # macOS/Linux: source .venv/bin/activate
pip install -r requirements-ml.txt  # or requirements.txt to skip the image model
python -m printscout                # opens http://127.0.0.1:8000
```

On Windows you can also double-click **`start.bat`**.

To run the tests:

```bash
pytest
```

## Project files

### `printscout/`

| File | What it does |
|---|---|
| `__main__.py` | Lets you start the app with `python -m printscout`. |
| `cli.py` | Starts the web server and opens the browser. |
| `config.py` | File locations and the settings you can change on the Settings page, saved in `data/settings.json`. |
| `db.py` | The SQLite database: the tables, and functions to save and read listings, scans and market data. |
| `api.py` | The FastAPI server. Every button in the app calls one of its endpoints. |
| `jobs.py` | Runs long tasks (scans, training, market checks) in the background so the page can show their progress. |
| `evaluator.py` | Scores each product, works out the costs and profit, checks the market and groups listings for the same product. |

### `printscout/scraper/`

| File | What it does |
|---|---|
| `http.py` | Downloads pages from eBay, with polite delays between requests and retries when eBay fails. |
| `parsers.py` | Reads eBay's HTML and pulls out each listing's title, price, sold count and seller. |
| `crawler.py` | The scan. It searches eBay, finds sellers of 3D-printed items, saves their listings and uses each title as a new search, using several threads at once. |

### `printscout/vision/`

| File | What it does |
|---|---|
| `autolabel.py` | Builds a training set from your scans: photos of listings that say "3D printed" and photos of ones that don't. |
| `model.py` | The image model (MobileNetV3 or ResNet18, pretrained on ImageNet) and how images are prepared for it. |
| `train.py` | Trains the model in two phases, keeps the best version and saves its accuracy, precision and recall. |
| `predict.py` | Loads the trained model and gives the chance that a photo shows a 3D-printed item. |

### `printscout/web/`

| File | What it does |
|---|---|
| `index.html` | The single page of the app, with a section for each tab. |
| `app.js` | Switches between pages, calls the API and fills in the tables and product panel. |
| `styles.css` | The styling. |

### `tests/`

Tests for the parsers, crawler, scoring, API and image model. The crawler
tests use a fake eBay client, so they run offline.

## Built with

Python, FastAPI, SQLite, PyTorch (MobileNetV3) and a plain HTML/CSS/JavaScript
front end.

## Responsible use

Please respect eBay's terms of service. PrintScout waits between requests by
default, so keep the delays reasonable and use it for your own market research.
