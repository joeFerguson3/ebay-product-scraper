"""End-to-end check of the training pipeline on a tiny synthetic dataset.

'3D printed' images get fine horizontal layer lines; the others are smooth
gradients. Real training uses your own photos - this just proves the
pipeline (split, two-phase training, metrics, checkpoint, inference) works.
"""
import json
import random

import pytest

torch = pytest.importorskip("torch")
from PIL import Image, ImageDraw  # noqa: E402


def synth(path, printed, seed):
    rnd = random.Random(seed)
    base = tuple(rnd.randint(40, 220) for _ in range(3))
    img = Image.new("RGB", (96, 96), base)
    d = ImageDraw.Draw(img)
    if printed:
        for y in range(0, 96, 3):
            d.line([(0, y), (95, y)], fill=tuple(max(0, c - 45) for c in base))
    else:
        for x in range(96):
            d.line([(x, 0), (x, 95)], fill=tuple(min(255, c + x // 3) for c in base))
    img.save(path)


def test_train_and_predict(tmp_path):
    from printscout.vision.predict import Detector
    from printscout.vision.train import binary_metrics, train
    ds = tmp_path / "ds"
    for cls, printed in (("3d_printed", True), ("not_3d_printed", False)):
        (ds / cls).mkdir(parents=True)
        for i in range(24):
            synth(ds / cls / f"{i}.png", printed, seed=i + (0 if printed else 1000))
    (ds / "3d_printed" / "broken.jpg").write_bytes(b"not an image")  # must be skipped safely

    model_path, metrics_path = tmp_path / "m.pt", tmp_path / "metrics.json"
    metrics = train(ds, model_path, epochs=4, head_epochs=1, batch_size=16, pretrained=False,
                    metrics_path=metrics_path, on_log=lambda m: None)
    assert model_path.exists() and json.loads(metrics_path.read_text())["best_epoch"] >= 1
    assert len(metrics["history"]) >= 1 and {"tp", "tn", "fp", "fn"} <= set(metrics["confusion"])

    det = Detector(model_path)
    p = det.predict_bytes((ds / "3d_printed" / "0.png").read_bytes())
    assert 0.0 <= p <= 1.0


def test_binary_metrics():
    from printscout.vision.train import binary_metrics
    m = binary_metrics([1, 1, 0, 0], [1, 0, 0, 1], positive=1)
    assert m["accuracy"] == 0.5 and m["precision"] == 0.5 and m["recall"] == 0.5
    assert m["confusion"] == {"tp": 1, "tn": 1, "fp": 1, "fn": 1}


def test_too_few_images_rejected(tmp_path):
    from printscout.vision.train import train
    for cls in ("3d_printed", "not_3d_printed"):
        (tmp_path / cls).mkdir()
        synth(tmp_path / cls / "a.png", cls == "3d_printed", 1)
    with pytest.raises(ValueError, match="at least"):
        train(tmp_path, tmp_path / "m.pt", epochs=1, pretrained=False, on_log=lambda m: None)
