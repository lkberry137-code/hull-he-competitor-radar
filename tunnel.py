"""
Starts the local preview dashboard server (127.0.0.1:8765) and opens a shareable
Cloudflare Quick Tunnel (https://*.trycloudflare.com) for colleagues.
"""

from __future__ import annotations

import functools
import http.server
import os
import re
import shutil
import socketserver
import subprocess
import threading
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DASH_DIR = BASE_DIR / "dashboard"
TUNNEL_URL_FILE = BASE_DIR / "tunnel_url.txt"
PORT = 8765


def find_cloudflared() -> str | None:
    candidates = [
        BASE_DIR / "cloudflared.exe",
        BASE_DIR.parent / "gibson-deal-radar" / "cloudflared.exe",
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    return shutil.which("cloudflared")


def start_dashboard_server() -> socketserver.TCPServer:
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(DASH_DIR))

    class ReusableTCPServer(socketserver.TCPServer):
        allow_reuse_address = True

    httpd = ReusableTCPServer(("127.0.0.1", PORT), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd


def main() -> None:
    cf_path = find_cloudflared()
    if not cf_path:
        print("ERROR: cloudflared.exe not found in hull-he-radar or gibson-deal-radar.")
        return

    httpd = start_dashboard_server()
    print(f"Local dashboard serving on http://127.0.0.1:{PORT}")
    print(f"Starting Cloudflare Tunnel using {cf_path} ...")

    if TUNNEL_URL_FILE.exists():
        TUNNEL_URL_FILE.unlink()

    proc = subprocess.Popen(
        [cf_path, "tunnel", "--url", f"http://127.0.0.1:{PORT}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            match = re.search(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com", line)
            if match:
                url = match.group(0)
                TUNNEL_URL_FILE.write_text(url, encoding="utf-8")
                print("\n" + "=" * 68)
                print("UNIVERSITY OF HULL — HE COMPETITOR RADAR LIVE SHARE LINK")
                print("=" * 68)
                print(f"\n  {url}\n")
                print("=" * 68)
                print("Share this link with colleagues (keep this window open while sharing).\n")
                break
        proc.wait()
    except KeyboardInterrupt:
        print("\nStopping tunnel and local server...")
        proc.terminate()
        httpd.shutdown()


if __name__ == "__main__":
    main()
