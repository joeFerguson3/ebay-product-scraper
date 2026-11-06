
from __future__ import annotations

import json
import random
import threading
from collections import Counter
from pathlib import Path
from typing import Callable

import torch
from PIL import Image
from torch import nn
from torch.utils.data import DataLoader, Subset
from torchvision.datasets import ImageFolder

from ..config import CLASS_POSITIVE, DATASET_DIR, METRICS_PATH, MODEL_PATH
from .autolabel import seller_of
from .model import best_device, build_model, eval_transform, set_backbone_trainable, train_transform

MIN_IMAGES_PER_CLASS = 10


class TrainingCancelled(Exception):
    pass


# some eBay images are PNGs with transparency or greyscale, the model wants 3 channels
def _rgb_loader(path: str) -> Image.Image:
    with Image.open(path) as img:
        return img.convert("RGB")


# one broken download would otherwise crash training halfway through an epoch
def _readable(path: str) -> bool:
    try:
        with Image.open(path) as img:
            img.verify()
        return True
    except Exception:
        return False


# splits the images into training and validation (returns two lists of positions).
# groups says which seller each image came from, and all of a seller's photos go to the
# same side, so near-duplicate photos can't be in both and make the score look better
def split_by_group(groups: list[str], val_fraction: float, seed: int) -> tuple[list[int], list[int]]:
    rng = random.Random(seed)
    target = max(1, round(len(groups) * val_fraction))  # how many validation images we want

    by_group = {}  # group -> positions of its images
    for i, g in enumerate(groups):
        by_group.setdefault(g, []).append(i)
    names = sorted(by_group)
    rng.shuffle(names)

    train, val = [], []
    for name in names:
        images = by_group[name]
        # fill validation up to roughly the target, without one big seller swamping it
        if len(val) < target and len(val) + len(images) <= target * 1.5:
            val += images
        else:
            train += images

    # e.g. every photo is from one seller, so split by photo instead
    if not train or not val:
        positions = list(range(len(groups)))
        rng.shuffle(positions)
        val, train = positions[:target], positions[target:]
    return train, val


# accuracy alone looks good on an unbalanced set, so precision/recall/F1 too.
# precision = of the ones it called printed, how many were. recall = of the printed ones, how many it found
def binary_metrics(y_true: list[int], y_pred: list[int], positive: int) -> dict:
    tp = sum(1 for t, p in zip(y_true, y_pred) if t == positive and p == positive)
    tn = sum(1 for t, p in zip(y_true, y_pred) if t != positive and p != positive)
    fp = sum(1 for t, p in zip(y_true, y_pred) if t != positive and p == positive)
    fn = sum(1 for t, p in zip(y_true, y_pred) if t == positive and p != positive)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "accuracy": (tp + tn) / max(1, len(y_true)),
        "precision": precision, "recall": recall, "f1": f1,
        "confusion": {"tp": tp, "tn": tn, "fp": fp, "fn": fn},
    }


# one pass over the data. with an optimizer it trains, without one it's just evaluating
def _run_epoch(model, loader, device, criterion, optimizer=None):
    training = optimizer is not None
    model.train(training)
    total_loss, y_true, y_pred = 0.0, [], []
    with torch.set_grad_enabled(training):
        for x, y in loader:
            x, y = x.to(device), y.to(device)
            logits = model(x)
            loss = criterion(logits, y)
            if training:
                optimizer.zero_grad()
                loss.backward()
                optimizer.step()
            total_loss += loss.item() * x.size(0)  # loss is a batch mean, weight by batch size
            y_true += y.tolist()
            y_pred += logits.argmax(1).tolist()
    return total_loss / max(1, len(y_true)), y_true, y_pred


def train(dataset_dir: Path = DATASET_DIR, out_path: Path = MODEL_PATH, *,
          arch: str = "mobilenet_v3_small", epochs: int = 12, head_epochs: int = 3,
          batch_size: int = 32, lr: float = 1e-3, fine_tune_lr: float = 1e-4,
          val_fraction: float = 0.2, patience: int = 4, pretrained: bool = True, seed: int = 42,
          metrics_path: Path = METRICS_PATH,
          on_log: Callable[[str], None] = print,
          on_progress: Callable[[float], None] = lambda p: None,
          stop_event: threading.Event | None = None) -> dict:
    torch.manual_seed(seed)

    # ImageFolder uses the folder names as the class labels
    base = ImageFolder(str(dataset_dir), loader=_rgb_loader)
    if CLASS_POSITIVE not in base.class_to_idx or len(base.classes) != 2:
        raise ValueError(f"Expected exactly two class folders in {dataset_dir}, "
                         f"one named '{CLASS_POSITIVE}'; found {base.classes}")
    base.samples = [s for s in base.samples if _readable(s[0])]
    base.targets = [t for _, t in base.samples]
    counts = Counter(base.targets)
    for cls, idx in base.class_to_idx.items():
        if counts[idx] < MIN_IMAGES_PER_CLASS:
            raise ValueError(f"Need at least {MIN_IMAGES_PER_CLASS} images in '{cls}', "
                             f"found {counts[idx]}")
    positive = base.class_to_idx[CLASS_POSITIVE]

    groups = [seller_of(path) for path, _ in base.samples]
    train_idx, val_idx = split_by_group(groups, val_fraction, seed)
    # same files loaded twice, once with augmentation for training and once without for validation
    train_ds = ImageFolder(str(dataset_dir), transform=train_transform(), loader=_rgb_loader)
    val_ds = ImageFolder(str(dataset_dir), transform=eval_transform(), loader=_rgb_loader)
    train_ds.samples = val_ds.samples = base.samples  # use the filtered list, so broken images are skipped
    train_loader = DataLoader(Subset(train_ds, train_idx), batch_size=batch_size, shuffle=True)
    val_loader = DataLoader(Subset(val_ds, val_idx), batch_size=batch_size)
    sizes = ", ".join(f"{counts[i]} {c}" for c, i in base.class_to_idx.items())
    on_log(f"Dataset has {sizes}. Using {len(train_idx)} for training and {len(val_idx)} for validation.")

    device = best_device()
    model = build_model(arch, num_classes=2, pretrained=pretrained).to(device)
    on_log(f"Training {arch} ({'pretrained on ImageNet' if pretrained else 'from scratch'}) on {device}")

    # Weight the loss by inverse class frequency so an unbalanced dataset
    # doesn't just teach the model to always predict the majority class.
    train_counts = Counter(base.targets[i] for i in train_idx)
    weights = torch.tensor([len(train_idx) / (2 * train_counts[c]) for c in range(2)],
                           dtype=torch.float, device=device)
    criterion = nn.CrossEntropyLoss(weight=weights)

    history, best, bad_epochs = [], None, 0
    head_epochs = min(head_epochs, epochs)
    for epoch in range(1, epochs + 1):
        if stop_event is not None and stop_event.is_set():
            raise TrainingCancelled()
        # phase 1: backbone frozen first so the new random head doesn't wreck the pretrained weights
        if epoch == 1:
            set_backbone_trainable(model, False)
            optimizer = torch.optim.AdamW([p for p in model.parameters() if p.requires_grad], lr=lr)
            phase = "head"
        # phase 2: unfreeze the lot, much lower lr so it only nudges the pretrained layers
        if epoch == head_epochs + 1:
            set_backbone_trainable(model, True)
            optimizer = torch.optim.AdamW(model.parameters(), lr=fine_tune_lr, weight_decay=1e-4)
            phase = "fine-tune"

        train_loss, t_true, t_pred = _run_epoch(model, train_loader, device, criterion, optimizer)
        val_loss, v_true, v_pred = _run_epoch(model, val_loader, device, criterion)
        tm = binary_metrics(t_true, t_pred, positive)
        vm = binary_metrics(v_true, v_pred, positive)
        history.append({"epoch": epoch, "phase": phase, "train_loss": train_loss,
                        "val_loss": val_loss, "train_acc": tm["accuracy"],
                        "val_acc": vm["accuracy"], "val_f1": vm["f1"]})
        on_log(f"Epoch {epoch} of {epochs} ({phase}). Training loss {train_loss:.3f}, validation loss "
               f"{val_loss:.3f}. Training accuracy {tm['accuracy']:.1%}, validation accuracy "
               f"{vm['accuracy']:.1%}, F1 {vm['f1']:.3f}")
        on_progress(epoch / epochs)

        # best = highest val F1, lower val loss breaks ties. only the best epoch gets saved to disk
        score = (vm["f1"], -val_loss)
        if best is None or score > best["score"]:
            best = {"score": score, "epoch": epoch, "metrics": vm}
            bad_epochs = 0
            out_path.parent.mkdir(parents=True, exist_ok=True)
            torch.save({"state_dict": model.state_dict(), "arch": arch,
                        "classes": base.classes, "positive_index": positive}, out_path)
        else:
            bad_epochs += 1
            # don't stop during the head phase, fine-tuning usually improves on it
            if epoch > head_epochs and bad_epochs >= patience:
                on_log(f"Stopping early, no improvement for {patience} epochs")
                break

    # read by the Detector page (scores and confusion matrix)
    metrics = {**best["metrics"], "best_epoch": best["epoch"], "arch": arch,
               "history": history, "val_size": len(val_idx)}
    metrics_path.parent.mkdir(parents=True, exist_ok=True)
    metrics_path.write_text(json.dumps(metrics, indent=2))
    on_log(f"Best model was epoch {best['epoch']} with accuracy {metrics['accuracy']:.1%}, "
           f"precision {metrics['precision']:.1%} and recall {metrics['recall']:.1%}. Saved to {out_path.name}")
    return metrics
