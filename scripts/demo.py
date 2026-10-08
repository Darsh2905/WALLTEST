#!/usr/bin/env python3
"""One command, any OS:  python scripts/demo.py   (needs Docker with the compose plugin)
Builds and starts PostgreSQL 16 + pgvector and the API/UI, runs migrations, seeds data, waits until healthy and opens the browser."""
import argparse
import json
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def run(cmd, **kw):
    print("$", " ".join(cmd), flush=True)
    return subprocess.run(cmd, cwd=ROOT, **kw)


def compose():
    for cmd in (["docker", "compose"], ["docker-compose"]):
        try:
            if subprocess.run(cmd + ["version"], capture_output=True).returncode == 0:
                return cmd
        except FileNotFoundError:
            pass
    sys.exit("Docker with the compose plugin is required: https://docs.docker.com/get-docker/")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="8000")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--down", action="store_true", help="stop the stack")
    ap.add_argument("--reset", action="store_true", help="stop and DELETE the database volume first")
    a = ap.parse_args()
    dc = compose()
    if a.down or a.reset:
        run(dc + ["down"] + (["-v"] if a.reset else []))
        if a.down:
            return
    if run(dc + ["up", "--build", "-d"]).returncode != 0:
        sys.exit("docker compose up failed")
    url = f"http://localhost:{a.port}"
    print(f"waiting for {url} ...", flush=True)
    t0 = time.time()
    while time.time() - t0 < 240:
        try:
            with urllib.request.urlopen(url + "/api/health", timeout=3) as r:
                h = json.load(r)
                if h.get("ok"):
                    print(f"healthy: {h['postgres'].split(',')[0]} · API login role {h['login_role']} (request role {h['request_role']})")
                    break
        except Exception:  # noqa: BLE001
            time.sleep(1.5)
    else:
        run(dc + ["logs", "--tail", "40", "api"])
        sys.exit("the API did not become healthy in time")
    print(f"\nWALLTEST is running at {url}\n  stop: python scripts/demo.py --down     wipe data: python scripts/demo.py --reset")
    if not a.no_browser:
        webbrowser.open(url)


if __name__ == "__main__":
    main()
