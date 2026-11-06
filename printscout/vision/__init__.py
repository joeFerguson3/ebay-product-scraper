"""3D-print image detector. Torch is imported lazily so the rest of the app
works (and starts fast) even if the ML dependencies are not installed."""
from importlib.util import find_spec


def torch_available() -> bool:
    # find_spec only checks it's installed, doesn't actually import it (slow)
    return find_spec("torch") is not None and find_spec("torchvision") is not None
