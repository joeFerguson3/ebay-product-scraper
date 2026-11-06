import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixture_html():
    return lambda name: (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture
def tmp_data(tmp_path, monkeypatch):
    """Point every data path at a temp dir so tests never touch real data."""
    from printscout import config
    from printscout.vision import autolabel
    paths = {
        "DATA_DIR": tmp_path / "data", "MODELS_DIR": tmp_path / "models",
        "DATASET_DIR": tmp_path / "data" / "training_images",
        "SETTINGS_PATH": tmp_path / "data" / "settings.json",
        "MODEL_PATH": tmp_path / "models" / "detector.pt",
        "METRICS_PATH": tmp_path / "models" / "metrics.json",
    }
    for k, v in paths.items():
        monkeypatch.setattr(config, k, v)
        if hasattr(autolabel, k):
            monkeypatch.setattr(autolabel, k, v)
    import printscout.api as api
    import printscout.scraper.crawler as crawler
    for mod in (api, crawler):
        for k, v in paths.items():
            if hasattr(mod, k):
                monkeypatch.setattr(mod, k, v)
    # load/save_settings take the path as a default arg, so patch the functions
    monkeypatch.setattr(api, "load_settings", lambda: config.load_settings(paths["SETTINGS_PATH"]))
    monkeypatch.setattr(api, "save_settings", lambda s: config.save_settings(s, paths["SETTINGS_PATH"]))
    return tmp_path
