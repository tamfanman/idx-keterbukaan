"""
Ringkasan kontekstual gratis untuk pengumuman Keterbukaan Informasi BEI.

Alur: unduh PDF tiap pengumuman (in-page fetch, lolos Cloudflare) -> ekstrak teks
(pypdf) -> kirim ke Google Gemini (free tier) -> simpan ringkasan ke record.
Dijalankan di PC setelah scrape.py. Inkremental: hanya memproses pengumuman yang
belum punya ringkasan, maksimal MAX_PER_RUN per jalan (hemat kuota & waktu).

Butuh API key Gemini gratis di file `.gemini_key` (root repo, tidak di-commit).
Ambil di: https://aistudio.google.com/apikey
"""

import base64
import io
import json
import time
import urllib.error
import urllib.request
import tempfile
from pathlib import Path

from patchright.sync_api import sync_playwright
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parent.parent
OUT_FILE = ROOT / "docs" / "announcements.json"
KEY_FILE = ROOT / ".gemini_key"
KETERBUKAAN_URL = "https://www.idx.co.id/id/perusahaan-tercatat/keterbukaan-informasi/"

MODEL = "gemini-2.0-flash"       # model free tier
MAX_PER_RUN = 15                 # batas dokumen per jalan (hemat kuota/waktu)
MIN_TEXT = 200                   # < ini dianggap PDF scan/tanpa teks
MAX_TEXT = 12000                 # potong teks panjang sebelum kirim ke AI
SLEEP_BETWEEN = 4.5              # detik antar panggilan (free tier ~15 req/menit)

PROMPT = (
    "Kamu asisten yang meringkas dokumen Keterbukaan Informasi Bursa Efek Indonesia. "
    "Ringkas isi dokumen berikut dalam 2-4 kalimat bahasa Indonesia yang jelas dan padat, "
    "fokus pada inti peristiwa dan dampak/relevansinya bagi investor. "
    "Jangan mengarang; hanya berdasarkan teks. Jangan sekadar mengulang judul.\n\n"
    "=== DOKUMEN ===\n"
)


def load_key() -> str:
    if not KEY_FILE.exists():
        raise SystemExit(f"[error] File API key tidak ada: {KEY_FILE}\n"
                         "Ambil key gratis di https://aistudio.google.com/apikey lalu simpan di file itu.")
    key = KEY_FILE.read_text(encoding="utf-8").strip()
    if not key:
        raise SystemExit(f"[error] {KEY_FILE} kosong.")
    return key


def gemini_summarize(text: str, key: str) -> str:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent?key={key}"
    body = json.dumps({
        "contents": [{"parts": [{"text": PROMPT + text[:MAX_TEXT]}]}],
        "generationConfig": {"temperature": 0.2, "maxOutputTokens": 400},
    }).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    for attempt in range(2):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                data = json.loads(r.read())
            return data["candidates"][0]["content"]["parts"][0]["text"].strip()
        except urllib.error.HTTPError as e:
            msg = e.read().decode("utf-8", "ignore")[:200]
            if e.code == 429 and attempt == 0:      # rate limit -> tunggu & ulang sekali
                print("[gemini] 429 rate limit, tunggu 20s...")
                time.sleep(20)
                continue
            raise RuntimeError(f"HTTP {e.code}: {msg}")
    raise RuntimeError("gagal setelah retry")


def fetch_pdf_text(page, url: str) -> str:
    res = page.evaluate("""async (u) => {
        const r = await fetch(u, {credentials:'include'});
        if (!r.ok) return {status:r.status, data:null};
        const buf = await r.arrayBuffer();
        let s=''; const b=new Uint8Array(buf); const CH=0x8000;
        for (let i=0;i<b.length;i+=CH) s+=String.fromCharCode.apply(null,b.subarray(i,i+CH));
        return {status:r.status, data: btoa(s)};
    }""", url)
    if res["status"] != 200 or not res["data"]:
        raise RuntimeError(f"unduh PDF gagal (status {res['status']})")
    raw = base64.b64decode(res["data"])
    reader = PdfReader(io.BytesIO(raw))
    return "\n".join((pg.extract_text() or "") for pg in reader.pages).strip()


def main() -> int:
    key = load_key()
    data = json.loads(OUT_FILE.read_text(encoding="utf-8"))
    anns = data.get("announcements", [])

    # Target: yang belum punya 'summary' dan punya lampiran PDF. Terbaru dulu.
    targets = [a for a in anns
               if not a.get("summary") and any(str(f.get("url", "")).lower().endswith(".pdf")
                                               for f in a.get("attachments", []))]
    targets = targets[:MAX_PER_RUN]
    if not targets:
        print("[done] tidak ada dokumen baru untuk diringkas.")
        return 0
    print(f"[info] {len(targets)} dokumen akan diringkas (maks {MAX_PER_RUN}/jalan).")

    done = 0
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=str(Path(tempfile.gettempdir()) / "pw-idx-profile"),
            channel="chrome", headless=False, no_viewport=True,
            args=["--no-sandbox", "--window-position=-2400,-2400", "--window-size=1200,900"])
        page = ctx.new_page()
        page.goto(KETERBUKAAN_URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(4000)  # CF clearance

        for a in targets:
            pdf = next((f for f in a["attachments"] if str(f.get("url", "")).lower().endswith(".pdf")), None)
            label = f'{a.get("kode_emiten","?")} — {(a.get("judul") or "")[:50]}'
            try:
                text = fetch_pdf_text(page, pdf["url"])
            except Exception as e:
                print(f"[skip] {label}: {e}")
                continue
            if len(text) < MIN_TEXT:
                a["summary"] = None
                a["summary_status"] = "scan"   # PDF gambar/scan, tak ada teks
                print(f"[scan] {label}")
                continue
            try:
                summary = gemini_summarize(text, key)
            except Exception as e:
                print(f"[gemini-err] {label}: {e}")
                continue
            a["summary"] = summary
            a["summary_status"] = "ok"
            done += 1
            print(f"[ok] {label}\n      -> {summary[:120]}...")
            time.sleep(SLEEP_BETWEEN)
        ctx.close()

    OUT_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[done] {done} ringkasan baru disimpan -> {OUT_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
