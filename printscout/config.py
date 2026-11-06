
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

# paths hang off the repo root, so it doesn't matter where the app is started from
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
MODELS_DIR = ROOT / "models"
DATASET_DIR = DATA_DIR / "training_images"
DB_PATH = DATA_DIR / "printscout.db"
SETTINGS_PATH = DATA_DIR / "settings.json"
MODEL_PATH = MODELS_DIR / "detector.pt"
METRICS_PATH = MODELS_DIR / "metrics.json"

# folder names under training_images/, one per class for the image model
CLASS_POSITIVE = "3d_printed"
CLASS_NEGATIVE = "not_3d_printed"

# the settings that weight each part of the score
WEIGHT_FIELDS = ("weight_demand", "weight_market", "weight_competition",
                 "weight_margin", "weight_efficiency")


# defaults are what a fresh install gets, settings.json overrides them
@dataclass
class Settings:
    # Scanner (eBay UK)
    required_phrase: str = "3d printed"
    excluded_phrases: list[str] = field(
        default_factory=lambda: ["laser", "prop", "replica", "resin kit"]
    )
    min_seller_feedback: int = 100    # only follow established sellers
    workers: int = 6
    request_delay: float = 1.0        # seconds before each request (jittered in the client)
    max_sellers: int = 150            # seller budget per scan
    check_descriptions: bool = True
    use_image_detector: bool = False
    detector_threshold: float = 0.6   # CNN probability needed to count as 3D printed

    # Evaluator (costs per unit)
    # Amazon UK fees (Pro plan): most categories are 15% with a £0.25 minimum
    fee_percent: float = 15.0           # referral fee
    fee_minimum: float = 0.25           # minimum fee per sale
    fee_fixed: float = 0.0              # fixed per-item fee (Individual plan: £0.75)
    # all £. per-hour ones are per hour of printing
    filament_cost_per_kg: float = 20.0
    electricity_cost_per_hour: float = 0.04
    machine_cost_per_hour: float = 0.10   # wear and spare parts
    packaging_cost: float = 0.50
    postage_cost: float = 0.0
    # used until real slicer estimates are entered for a product
    default_grams: float = 60.0
    default_print_hours: float = 2.5

    # Ranking weights (relative - they are normalised to add up to 100%)
    weight_demand: float = 25.0       # units sold on the listing
    weight_market: float = 15.0       # sold across eBay in the last ~90 days
    weight_competition: float = 15.0  # other eBay listings - more = proven market, as I sell on Amazon
    weight_margin: float = 15.0       # profit / price
    weight_efficiency: float = 15.0   # profit per printer-hour
    ip_risk_penalty: float = 15.0     # points off the 0-100 score for franchise/character items

    # makes Settings from a dictionary (settings.json or the settings form)
    @classmethod
    def from_dict(cls, data: dict) -> "Settings":
        # ignore keys that aren't settings any more, so an old settings.json still loads
        names = [f.name for f in fields(cls)]
        s = cls(**{k: v for k, v in data.items() if k in names})
        s.validate()
        return s

    # fixes the types and keeps numbers in a sensible range
    def validate(self) -> None:
        self.workers = max(1, min(int(self.workers), 32))
        self.max_sellers = max(1, int(self.max_sellers))
        self.min_seller_feedback = max(0, int(self.min_seller_feedback))
        self.request_delay = max(0.0, float(self.request_delay))
        self.detector_threshold = min(0.99, max(0.01, float(self.detector_threshold)))
        self.ip_risk_penalty = min(100.0, max(0.0, float(self.ip_risk_penalty)))
        self.fee_percent = max(0.0, float(self.fee_percent))
        self.fee_minimum = max(0.0, float(self.fee_minimum))
        self.fee_fixed = max(0.0, float(self.fee_fixed))
        for name in WEIGHT_FIELDS:
            setattr(self, name, min(100.0, max(0.0, float(getattr(self, name)))))
        self.required_phrase = self.required_phrase.strip().lower()
        # the settings form sends the excluded phrases as one comma separated string
        if isinstance(self.excluded_phrases, str):
            self.excluded_phrases = self.excluded_phrases.split(",")
        self.excluded_phrases = [p.strip().lower() for p in self.excluded_phrases if p.strip()]

    # the weights scaled to add up to 1, e.g. {"demand": 0.25, "market": 0.15, ...}
    def weights(self) -> dict[str, float]:
        total = sum(getattr(self, name) for name in WEIGHT_FIELDS) or 1.0  # or 1 so all zero doesn't divide by 0
        result = {}
        for name in WEIGHT_FIELDS:
            result[name.removeprefix("weight_")] = getattr(self, name) / total
        return result

    def to_dict(self) -> dict:
        return asdict(self)


# reads settings.json, or the defaults if it's missing or broken
def load_settings(path: Path = SETTINGS_PATH) -> Settings:
    if path.exists():
        try:
            return Settings.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (ValueError, TypeError):
            pass  # broken json or bad values, use the defaults rather than crash
    return Settings()


def save_settings(settings: Settings, path: Path = SETTINGS_PATH) -> None:
    settings.validate()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings.to_dict(), indent=2), encoding="utf-8")


# makes the data folders on a fresh install
def ensure_dirs() -> None:
    for d in (DATA_DIR, MODELS_DIR, DATASET_DIR / CLASS_POSITIVE, DATASET_DIR / CLASS_NEGATIVE):
        d.mkdir(parents=True, exist_ok=True)
