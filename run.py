#!/usr/bin/env python3
"""Start the Markdown Voice Player.

    python run.py                 # http://127.0.0.1:8000
    python run.py --port 9000
    python run.py --engine demo   # offline tone engine, for testing the UI
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
import webbrowser


def main() -> int:
    parser = argparse.ArgumentParser(description="Markdown -> voice player")
    parser.add_argument("--host", default=os.environ.get("MDVOICE_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("MDVOICE_PORT", "8000")))
    parser.add_argument("--engine", default=os.environ.get("MDVOICE_ENGINE", "edge"),
                        choices=["edge", "demo"])
    parser.add_argument("--no-browser", action="store_true", help="do not open a browser tab")
    parser.add_argument("--reload", action="store_true", help="auto-reload on code changes")
    args = parser.parse_args()

    os.environ["MDVOICE_ENGINE"] = args.engine
    os.environ["MDVOICE_HOST"] = args.host
    os.environ["MDVOICE_PORT"] = str(args.port)

    try:
        import uvicorn
    except ImportError:
        print("Missing dependencies. Run:  pip install -r requirements.txt", file=sys.stderr)
        return 1

    url = f"http://{'127.0.0.1' if args.host in ('0.0.0.0', '::') else args.host}:{args.port}"
    print(f"\n  Markdown Voice Player  ->  {url}")
    print(f"  Engine: {args.engine}\n")
    if not args.no_browser:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()

    uvicorn.run(
        "mdvoice.server:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="warning",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
