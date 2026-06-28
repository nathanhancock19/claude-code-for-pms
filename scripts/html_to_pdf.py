"""
HTML to PDF Converter — Chrome Headless
========================================
Converts an HTML file to PDF using Chrome's headless renderer.
Produces pixel-perfect output with no header/footer artefacts.

Chrome must be installed at the standard Windows path:
    C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe

Usage:
    python scripts/html_to_pdf.py <path-to-html-file>

Example:
    python scripts/html_to_pdf.py outputs/502-paint-rca-rectification-proposal-rev1.html

Output:
    PDF saved alongside the HTML file with the same name and .pdf extension.
    e.g. outputs/502-paint-rca-rectification-proposal-rev1.pdf

Note:
    Previously used Gotenberg (Docker). Switched to Chrome headless after Docker Desktop
    was removed. Chrome must be installed; no other dependencies required.
"""

import os
import sys
import subprocess
import tempfile
import shutil
import re
import base64
import mimetypes
from pathlib import Path

_CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    "/usr/bin/google-chrome-stable",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium-browser",
    "/usr/bin/chromium",
]
# Honour an explicit CHROME_PATH env override; else pick the first that exists;
# else fall back to the Windows default. Lets the same script render on the VPS.
CHROME_PATH = os.environ.get("CHROME_PATH") or next(
    (p for p in _CHROME_CANDIDATES if Path(p).exists()), _CHROME_CANDIDATES[0]
)


def inline_local_images(html: str, base_dir: Path) -> str:
    """Replace local src='...' image references with base64 data URIs."""
    def replacer(match):
        src = match.group(1)
        if src.startswith("data:") or src.startswith("http://") or src.startswith("https://"):
            return match.group(0)
        img_path = (base_dir / src).resolve()
        if not img_path.exists():
            print(f"  Warning: asset not found, skipping: {img_path}")
            return match.group(0)
        mime, _ = mimetypes.guess_type(str(img_path))
        if not mime:
            mime = "application/octet-stream"
        b64 = base64.b64encode(img_path.read_bytes()).decode("ascii")
        return f'src="data:{mime};base64,{b64}"'
    return re.sub(r'src="([^"]+)"', replacer, html)


def convert(html_path: str) -> None:
    src = Path(html_path).resolve()

    if not src.exists():
        print(f"Error: file not found — {src}")
        sys.exit(1)

    chrome = Path(CHROME_PATH)
    if not chrome.exists():
        print(f"Error: Chrome not found at {CHROME_PATH}")
        sys.exit(1)

    out = src.with_suffix(".pdf")

    # Inline all local images as base64 so Chrome can render them from a temp file
    html = src.read_text(encoding="utf-8")
    html = inline_local_images(html, src.parent)

    with tempfile.TemporaryDirectory() as tmp:
        tmp_html = Path(tmp) / "index.html"
        tmp_pdf  = Path(tmp) / "index.pdf"
        tmp_html.write_text(html, encoding="utf-8")

        cmd = [
            str(chrome),
            "--headless=new",
            "--disable-gpu",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--no-pdf-header-footer",
            "--disable-extensions",
            "--run-all-compositor-stages-before-draw",
            f"--print-to-pdf={tmp_pdf}",
            tmp_html.as_uri(),
        ]

        result = subprocess.run(cmd, capture_output=True, text=True)

        if not tmp_pdf.exists():
            print("Error: Chrome did not produce a PDF.")
            print("stderr:", result.stderr[:500])
            sys.exit(1)

        shutil.copy(tmp_pdf, out)

    print(f"PDF saved: {out}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python scripts/html_to_pdf.py <path-to-html-file>")
        sys.exit(1)
    convert(sys.argv[1])
