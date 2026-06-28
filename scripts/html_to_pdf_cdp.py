"""
HTML to PDF via Chrome DevTools Protocol (CDP)
================================================
Generates clean PDFs with no browser headers, footers, timestamps or file paths.
Uses Chrome's Page.printToPDF command directly — same engine as Gotenberg.

Usage:
    python scripts/html_to_pdf_cdp.py <path-to-html-file> [<path-to-html-file> ...]

Example:
    python scripts/html_to_pdf_cdp.py outputs/lane-cove-eastern-gantry-bolt-cert-L2.html

Output:
    PDF saved alongside the HTML file with .pdf extension.

Requirements:
    pip install requests websocket-client
"""

import sys
import base64
import json
import time
import subprocess
import pathlib
import urllib.request
import urllib.error

import requests
import websocket

CHROME_PATH = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
DEBUG_PORT = 9223  # Use non-default port to avoid conflicts


def start_chrome(url: str) -> subprocess.Popen:
    cmd = [
        CHROME_PATH,
        f"--remote-debugging-port={DEBUG_PORT}",
        "--headless=new",
        "--disable-gpu",
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--run-all-compositor-stages-before-draw",
        f"--remote-allow-origins=http://localhost:{DEBUG_PORT}",
        url,
    ]
    return subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def get_ws_url(retries=15) -> str:
    for i in range(retries):
        try:
            resp = urllib.request.urlopen(
                f"http://localhost:{DEBUG_PORT}/json/list", timeout=2
            )
            tabs = json.loads(resp.read())
            for tab in tabs:
                if tab.get("type") == "page" and "webSocketDebuggerUrl" in tab:
                    return tab["webSocketDebuggerUrl"]
        except Exception:
            pass
        time.sleep(0.5)
    raise RuntimeError("Could not connect to Chrome DevTools")


def cdp_call(ws, method, params=None, msg_id=1):
    ws.send(json.dumps({"id": msg_id, "method": method, "params": params or {}}))
    while True:
        raw = ws.recv()
        msg = json.loads(raw)
        if msg.get("id") == msg_id:
            return msg.get("result", {})


def convert(html_path: str) -> None:
    src = pathlib.Path(html_path).resolve()
    if not src.exists():
        print(f"Error: file not found — {src}")
        sys.exit(1)

    file_url = src.as_uri()
    out = src.with_suffix(".pdf")

    print(f"Converting: {src.name}")

    proc = start_chrome(file_url)
    try:
        ws_url = get_ws_url()
        ws = websocket.create_connection(ws_url, timeout=30)

        # Wait for page load
        cdp_call(ws, "Page.enable", msg_id=1)
        time.sleep(2)  # allow fonts and images to load

        result = cdp_call(ws, "Page.printToPDF", {
            "printBackground": True,
            "displayHeaderFooter": False,  # ← no timestamps, no file paths
            "headerTemplate": "",
            "footerTemplate": "",
            "paperWidth": 8.27,    # A4 inches
            "paperHeight": 11.69,
            "marginTop": 0,
            "marginBottom": 0,
            "marginLeft": 0,
            "marginRight": 0,
            "preferCSSPageSize": True,
        }, msg_id=2)

        pdf_bytes = base64.b64decode(result["data"])
        ws.close()
        try:
            out.write_bytes(pdf_bytes)
        except PermissionError:
            out = out.parent / (out.stem + "-new.pdf")
            out.write_bytes(pdf_bytes)
            print(f"  (original locked — saved as {out.name})")
        print(f"  → {out.name} ({len(pdf_bytes):,} bytes)")

    finally:
        proc.terminate()
        proc.wait()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/html_to_pdf_cdp.py <html-file> [<html-file> ...]")
        sys.exit(1)
    for path in sys.argv[1:]:
        convert(path)
    print("All done.")
