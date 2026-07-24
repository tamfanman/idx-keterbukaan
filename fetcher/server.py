"""
Server lokal untuk IDX Keterbukaan Informasi.

Menyediakan UI + tombol on-demand:
- POST /api/refresh          -> scrape data terbaru (scrape.run) lalu push ke GitHub
- POST /api/summarize {code} -> ringkas 1 emiten on-demand (summarize.summarize_company) + push
- GET  /api/data             -> announcements.json
- GET  /api/health           -> penanda "ada backend" (dipakai frontend membedakan
                                app lokal vs situs publik read-only)

Jalankan: python fetcher/server.py  -> buka http://localhost:8080
Hanya bind ke 127.0.0.1 (aman; hanya bisa diakses di PC ini).
"""

import shutil
import subprocess
import threading
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

import scrape
import summarize

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
GIT = shutil.which("git") or r"D:\Apps\Git\cmd\git.exe"
PORT = 8080

app = Flask(__name__, static_folder=str(DOCS), static_url_path="")
_lock = threading.Lock()   # serialkan operasi browser (scrape/summarize) satu per satu


def git_push(msg: str) -> None:
    """Commit docs/announcements.json & push (agar situs publik ikut ter-update)."""
    try:
        subprocess.run([GIT, "-C", str(ROOT), "add", "docs/announcements.json"],
                       capture_output=True)
        r = subprocess.run([GIT, "-C", str(ROOT), "commit", "-m", msg],
                           capture_output=True, text=True)
        if r.returncode == 0:                      # ada perubahan -> push
            subprocess.run([GIT, "-C", str(ROOT), "push", "origin", "main"],
                           capture_output=True)
            print(f"[git] pushed: {msg}")
        else:
            print("[git] tidak ada perubahan.")
    except Exception as e:
        print(f"[git] gagal push: {e}")


@app.get("/")
def index():
    return send_from_directory(DOCS, "index.html")


@app.get("/api/health")
def health():
    return jsonify(ok=True)


@app.get("/api/data")
def data():
    return send_from_directory(DOCS, "announcements.json")


@app.post("/api/refresh")
def refresh():
    with _lock:
        try:
            info = scrape.run()
        except Exception as e:
            return jsonify(ok=False, error=str(e)), 500
    git_push(f"data: refresh manual (+{info['added']} baru, total {info['total']})")
    return jsonify(ok=True, **info)


@app.post("/api/summarize")
def summarize_ep():
    code = ((request.get_json(silent=True) or {}).get("code") or "").strip().upper()
    if not code:
        return jsonify(ok=False, error="kode emiten kosong"), 400
    with _lock:
        try:
            res = summarize.summarize_company(code)
        except SystemExit as e:                    # mis. API key tak ada
            return jsonify(ok=False, error=str(e)), 500
        except Exception as e:
            return jsonify(ok=False, error=str(e)), 500
    git_push(f"data: ringkas {code}")
    return jsonify(ok=True, **res)


if __name__ == "__main__":
    print(f"\n  IDX Keterbukaan Informasi — app lokal")
    print(f"  Buka di browser:  http://localhost:{PORT}\n")
    app.run(host="127.0.0.1", port=PORT, threaded=True)
