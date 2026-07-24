"""
Ringkasan kontekstual gratis (via Gemini) untuk pengumuman Keterbukaan Informasi BEI.

Dipakai dua cara:
- On-demand dari server lokal: `summarize_company(code)` -> ringkas dokumen aksi/lap
  milik satu emiten (yang belum diringkas) lalu buat rangkuman level-perusahaan.
- Batch CLI: `python summarize.py` -> ringkas semua dokumen aksi/lap + semua perusahaan.

HANYA kategori 'aksi' & 'lap' yang diringkas (spam & notsure di-skip).
Butuh API key Gemini gratis di `.gemini_key` (root repo, tidak di-commit).
"""

import base64
import io
import json
import os
import time
import urllib.error
import urllib.request
import tempfile
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from patchright.sync_api import sync_playwright
from pypdf import PdfReader

from categorize import classify

ROOT = Path(__file__).resolve().parent.parent
OUT_FILE = ROOT / "docs" / "announcements.json"
KEY_FILE = ROOT / ".gemini_key"
KETERBUKAAN_URL = "https://www.idx.co.id/id/perusahaan-tercatat/keterbukaan-informasi/"

MODEL = "gemini-flash-lite-latest"
CATS_TO_SUMMARIZE = {"aksi"}   # HANYA Aksi Korporasi yang diringkas
MAX_PER_RUN = int(os.environ.get("IDX_MAX_SUM", "15"))
MAX_COMPANY = int(os.environ.get("IDX_MAX_COMPANY", "12"))
MIN_TEXT = 200
MAX_TEXT = 12000
SLEEP_BETWEEN = 4.5

PROMPT_DOC = (
    "Kamu asisten yang meringkas dokumen Keterbukaan Informasi Bursa Efek Indonesia. "
    "Ringkas isi dokumen berikut dalam 2-4 kalimat bahasa Indonesia yang jelas dan padat, "
    "fokus pada inti peristiwa dan dampak/relevansinya bagi investor. "
    "Jangan mengarang; hanya berdasarkan teks. Jangan sekadar mengulang judul.\n\n"
    "=== DOKUMEN ===\n"
)
PROMPT_COMPANY = (
    "Kamu meringkas aktivitas Keterbukaan Informasi sebuah emiten BEI berkode {code}. "
    "Di bawah ini ringkasan tiap pengumuman Aksi Korporasi emiten tersebut. "
    "Buat SATU rangkuman menyeluruh 3-6 kalimat bahasa Indonesia yang MENYATUKAN poin-poin "
    "penting lintas pengumuman: tema utama, aksi korporasi/transaksi signifikan, dan hal "
    "yang relevan bagi investor. Sintesiskan, jangan sekadar menyalin. Jika hanya ada satu "
    "pengumuman, ringkas itu saja.\n\n=== RINGKASAN TIAP PENGUMUMAN ===\n{bullets}\n"
)


# ---------- util ----------
def load_key() -> str:
    if not KEY_FILE.exists() or not KEY_FILE.read_text(encoding="utf-8").strip():
        raise SystemExit(f"[error] API key Gemini tidak ada/kosong: {KEY_FILE}\n"
                         "Ambil gratis di https://aistudio.google.com/apikey")
    return KEY_FILE.read_text(encoding="utf-8").strip()


def _load_data() -> dict:
    return json.loads(OUT_FILE.read_text(encoding="utf-8"))


def _save_data(data: dict) -> None:
    OUT_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def cat_of(a) -> str:
    return a.get("kategori") or classify(a.get("judul"), a.get("perihal"))


def gemini(prompt_text: str, key: str, max_tokens: int = 400) -> str:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent?key={key}"
    body = json.dumps({
        "contents": [{"parts": [{"text": prompt_text}]}],
        "generationConfig": {"temperature": 0.2, "maxOutputTokens": max_tokens},
    }).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    for attempt in range(2):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                data = json.loads(r.read())
            cand = data["candidates"][0]
            txt = "".join(p.get("text", "") for p in cand.get("content", {}).get("parts", [])).strip()
            if not txt:
                raise RuntimeError(f"balasan kosong (finish={cand.get('finishReason')})")
            return txt
        except urllib.error.HTTPError as e:
            msg = e.read().decode("utf-8", "ignore")[:200]
            if e.code == 429 and attempt == 0:
                print("[gemini] 429 rate limit, tunggu 20s..."); time.sleep(20); continue
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
    reader = PdfReader(io.BytesIO(base64.b64decode(res["data"])))
    return "\n".join((pg.extract_text() or "") for pg in reader.pages).strip()


@contextmanager
def _browser_page():
    """Buka Chrome (headed di luar layar, lolos Cloudflare) & halaman IDX siap-fetch."""
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=str(Path(tempfile.gettempdir()) / "pw-idx-profile"),
            channel="chrome", headless=False, no_viewport=True,
            args=["--no-sandbox", "--window-position=-2400,-2400", "--window-size=1200,900"])
        page = ctx.new_page()
        page.goto(KETERBUKAAN_URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(4000)  # CF clearance
        try:
            yield page
        finally:
            ctx.close()


def _summarize_docs_with_page(page, docs, key) -> int:
    """MAP: ringkasan per-dokumen (ubah field 'summary'/'summary_status' in-place)."""
    done = 0
    for a in docs:
        pdf = next((f for f in a["attachments"] if str(f.get("url", "")).lower().endswith(".pdf")), None)
        label = f'{a.get("kode_emiten","?")} — {(a.get("judul") or "")[:45]}'
        try:
            text = fetch_pdf_text(page, pdf["url"])
        except Exception as e:
            print(f"[skip] {label}: {e}"); continue
        if len(text) < MIN_TEXT:
            a["summary"] = None; a["summary_status"] = "scan"; print(f"[scan] {label}"); continue
        try:
            a["summary"] = gemini(PROMPT_DOC + text[:MAX_TEXT], key)
            a["summary_status"] = "ok"; done += 1
            print(f"[ok] {label} -> {a['summary'][:90]}...")
        except Exception as e:
            print(f"[gemini-err] {label}: {e}"); continue
        time.sleep(SLEEP_BETWEEN)
    return done


def _build_company_summary(code, rows, company_summaries, key, force=False):
    """REDUCE: gabungkan ringkasan dok (yang sudah ada) jadi rangkuman perusahaan."""
    have = [a for a in rows if a.get("summary")]
    if not have:
        return None
    have.sort(key=lambda r: str(r.get("tanggal") or ""), reverse=True)
    sig = sorted([r.get("id") for r in have if r.get("id")])
    prev = company_summaries.get(code)
    if prev and prev.get("based_on") == sig and not force:
        return prev.get("summary")
    bullets = "\n".join(
        f"- [{(r.get('tanggal') or '')[:10]}] {r.get('judul','')}: {r.get('summary','')}" for r in have)
    csum = gemini(PROMPT_COMPANY.format(code=code, bullets=bullets), key, max_tokens=600)
    company_summaries[code] = {
        "summary": csum, "based_on": sig, "doc_count": len(have),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    return csum


# ---------- API on-demand (dipakai server lokal) ----------
def summarize_company(code: str) -> dict:
    """Ringkas SATU emiten saat diklik: proses dokumen aksi/lap yang belum diringkas,
    lalu buat/perbarui rangkuman level-perusahaan. Mengembalikan hasilnya."""
    code = (code or "").strip().upper()
    if not code:
        return {"code": code, "company_summary": None, "note": "kode kosong"}
    key = load_key()
    data = _load_data()
    anns = data.get("announcements", [])
    company_summaries = data.setdefault("company_summaries", {})

    rows = [a for a in anns
            if (a.get("kode_emiten") or "").strip().upper() == code and cat_of(a) in CATS_TO_SUMMARIZE]
    if not rows:
        return {"code": code, "company_summary": None, "note": "no_eligible",
                "message": "Tidak ada pengumuman Aksi Korporasi untuk emiten ini."}

    todo = [a for a in rows
            if not a.get("summary") and a.get("summary_status") != "scan"
            and any(str(f.get("url", "")).lower().endswith(".pdf") for f in a.get("attachments", []))]
    if todo:
        with _browser_page() as page:
            _summarize_docs_with_page(page, todo, key)

    csum = _build_company_summary(code, rows, company_summaries, key, force=bool(todo))
    _save_data(data)
    return {"code": code, "company_summary": csum, "doc_count": len(rows)}


# ---------- Batch CLI (backup manual) ----------
def summarize_all() -> dict:
    key = load_key()
    data = _load_data()
    anns = data.get("announcements", [])
    company_summaries = data.setdefault("company_summaries", {})

    targets = [a for a in anns
               if cat_of(a) in CATS_TO_SUMMARIZE and not a.get("summary")
               and a.get("summary_status") != "scan"
               and any(str(f.get("url", "")).lower().endswith(".pdf") for f in a.get("attachments", []))][:MAX_PER_RUN]
    doc_done = 0
    if targets:
        print(f"[map] {len(targets)} dokumen aksi/lap akan diringkas.")
        with _browser_page() as page:
            doc_done = _summarize_docs_with_page(page, targets, key)
    else:
        print("[map] tidak ada dokumen baru untuk diringkas.")

    groups = defaultdict(list)
    for a in anns:
        if cat_of(a) in CATS_TO_SUMMARIZE and a.get("summary"):
            code = (a.get("kode_emiten") or "").strip().upper()
            if code:
                groups[code].append(a)
    comp_done = 0
    for code, rows in groups.items():
        if comp_done >= MAX_COMPANY:
            break
        prev = company_summaries.get(code)
        sig = sorted([r.get("id") for r in rows if r.get("summary") and r.get("id")])
        if prev and prev.get("based_on") == sig:
            continue
        try:
            _build_company_summary(code, rows, company_summaries, key)
            comp_done += 1
        except Exception as e:
            print(f"[company-err] {code}: {e}")

    _save_data(data)
    print(f"[done] {doc_done} ringkasan dokumen + {comp_done} ringkasan perusahaan.")
    return {"docs": doc_done, "companies": comp_done}


def main() -> int:
    try:
        summarize_all()
        return 0
    except Exception as e:
        print(f"[error] {e}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
