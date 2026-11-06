
from __future__ import annotations

import torch
from torch import nn
from torchvision import models, transforms

IMG_SIZE = 224
# ImageNet mean/std, pretrained weights expect inputs normalised the same way
MEAN = (0.485, 0.456, 0.406)
STD = (0.229, 0.224, 0.225)


# the pretrained network with its last layer swapped for one with 2 outputs (printed / not)
def build_model(arch: str = "mobilenet_v3_small", num_classes: int = 2,
                pretrained: bool = True) -> nn.Module:
    # load the pretrained net, then swap the 1000-class ImageNet output layer for a 2-class one.
    # classifier[3] is the last Linear in mobilenet, resnet calls it fc
    if arch == "mobilenet_v3_small":
        weights = models.MobileNet_V3_Small_Weights.DEFAULT if pretrained else None
        model = models.mobilenet_v3_small(weights=weights)
        model.classifier[3] = nn.Linear(model.classifier[3].in_features, num_classes)
    elif arch == "mobilenet_v3_large":
        weights = models.MobileNet_V3_Large_Weights.DEFAULT if pretrained else None
        model = models.mobilenet_v3_large(weights=weights)
        model.classifier[3] = nn.Linear(model.classifier[3].in_features, num_classes)
    elif arch == "resnet18":
        weights = models.ResNet18_Weights.DEFAULT if pretrained else None
        model = models.resnet18(weights=weights)
        model.fc = nn.Linear(model.fc.in_features, num_classes)
    else:
        raise ValueError(f"Unknown model {arch}")
    return model


# freeze (False) / unfreeze (True) everything but the head, used by the two training phases.
# the head (the new last layers) always trains
def set_backbone_trainable(model: nn.Module, trainable: bool) -> None:
    for p in model.parameters():
        p.requires_grad = trainable
    head = model.fc if hasattr(model, "fc") else model.classifier  # resnet calls it fc
    for p in head.parameters():
        p.requires_grad = True


def train_transform() -> transforms.Compose:
    # random crops/flips/colour changes so it copes with how different eBay photos are,
    # and doesn't just memorise the few hundred training images
    return transforms.Compose([
        transforms.RandomResizedCrop(IMG_SIZE, scale=(0.6, 1.0)),
        transforms.RandomHorizontalFlip(),
        transforms.RandomRotation(12),
        transforms.ColorJitter(brightness=0.25, contrast=0.25, saturation=0.2, hue=0.02),
        transforms.ToTensor(),
        transforms.Normalize(MEAN, STD),
    ])


def eval_transform() -> transforms.Compose:
    # no randomness for validation/prediction, just resize a bit bigger and centre crop
    return transforms.Compose([
        transforms.Resize(int(IMG_SIZE * 1.14)),
        transforms.CenterCrop(IMG_SIZE),
        transforms.ToTensor(),
        transforms.Normalize(MEAN, STD),
    ])


# nvidia gpu, then apple silicon, else cpu
def best_device() -> torch.device:
    if torch.cuda.is_available():
        return torch.device("cuda")
    if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
        return torch.device("mps")
    return torch.device("cpu")
