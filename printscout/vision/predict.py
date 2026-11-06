"""Load a trained detector and score images."""
from __future__ import annotations

import io
import threading
from pathlib import Path

import torch
from PIL import Image

from ..config import MODEL_PATH
from .model import best_device, build_model, eval_transform


class Detector:
    """Returns the probability (0-1) that an image shows a 3D-printed item."""

    def __init__(self, path: Path = MODEL_PATH):
        # checkpoint saved by train.py: weights plus arch/class names so it can be rebuilt the same way
        ckpt = torch.load(path, map_location="cpu", weights_only=False)
        self.device = best_device()
        self.positive = ckpt["positive_index"]  # which of the 2 outputs means "3D printed"
        # pretrained=False, no point downloading ImageNet weights that get overwritten straight away
        self.model = build_model(ckpt["arch"], num_classes=2, pretrained=False)
        self.model.load_state_dict(ckpt["state_dict"])
        self.model.to(self.device).eval()
        self.transform = eval_transform()
        self.mtime = path.stat().st_mtime  # get_detector compares this to spot a retrained model
        self._lock = threading.Lock()  # one image at a time, the scanner calls this from several threads

    @torch.inference_mode()  # not training, so torch doesn't need to track gradients
    def predict_image(self, image: Image.Image) -> float:
        # unsqueeze makes it a batch of 1, softmax turns the two outputs into probabilities
        x = self.transform(image.convert("RGB")).unsqueeze(0).to(self.device)
        with self._lock:
            probs = torch.softmax(self.model(x), dim=1)[0]
        return float(probs[self.positive])

    def predict_bytes(self, data: bytes) -> float:
        with Image.open(io.BytesIO(data)) as img:
            return self.predict_image(img)


_cached: Detector | None = None


# the trained detector, or None if there isn't one yet.
# loading the model is slow so keep one around, but if the file on disk is newer
# (just retrained from the app) swap it in without needing a restart
def get_detector(path: Path = MODEL_PATH) -> Detector | None:
    global _cached
    if not path.exists():
        return None
    if _cached is None or _cached.mtime != path.stat().st_mtime:
        _cached = Detector(path)
    return _cached
