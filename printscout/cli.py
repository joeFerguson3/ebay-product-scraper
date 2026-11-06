
from __future__ import annotations

import logging
import threading
import webbrowser

import uvicorn

from .api import create_app

HOST, PORT = "127.0.0.1", 8000


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    url = f"http://{HOST}:{PORT}"
    print(f"PrintScout running at {url}  (Ctrl+C to quit)")
    # small delay so the browser doesn't open before the server is listening
    threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    # ws="none" because requests-html pins websockets 10.4, which is too old for uvicorn.
    # the UI polls instead of using websockets so nothing is lost
    uvicorn.run(create_app(), host=HOST, port=PORT, log_level="warning", ws="none")
