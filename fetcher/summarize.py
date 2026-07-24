"""
Ringkasan kontekstual gratis untuk pengumuman Keterbukaan Informasi BEI (via Gemini).

Dua tahap:
1. MAP  - ringkasan PER-DOKUMEN: unduh PDF (in-page fetch, lolos Cloudflare) ->
          ekstrak teks (pypdf) -> Gemini. HANYA kategori 'aksi' & 'lap'
          (Spam & Not Sure di-skip). Inkremental, maks MAX_PER_RUN/jalan.
2. REDUCE - ringkasan LEVEL-PERUSAHAAN: gabungkan ringkasan semua dokumen aksi/lap
          milik satu emiten jadi SATU rangkuman menyeluruh. Regenerasi hanya bila
          kumpulan dokumennya berubah.

Butuh API key Gemini gratis di file `.gemini_key` (root repo, tidak di-commit).
Ambil di: https://aistudio.google.com/apikey
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
from datetime import datetime, timezone
from pathlib import Path

from patchright.sync_api import sync_playwright
from pypdf import PdfReader

from categorize import classify

ROOT = Path(__file__).resolve().parent.parent
OUT_FILE = ROOT / "docs" / "announcements.json"
KEY_FILE = ROOT / ".gemini_key"
KETERBUKAAN_URL = "https://www.idx.co.id/id/perusahaan-tercatat/keterbukaan-informasi/"

MODEL = "gemini-flash-lite-latest"   # free tier; "latest" = tahan ganti versi
CATS_TO_SUMMARIZE = {"aksi", "lap"}  # HANYA ini yang diringkas (bukan spam/notsure)
MAX_PER_RUN = int(os.environ.get("IDX_MAX_SUM", "15"))       # dokumen/jalan
MAX_COMPANY = int(os.environ.get("IDX_MAX_COMPANY", "12"))   # ringkasan perusahaan/jalan
MIN_TEXT = 200            # < ini dianggap PDF scan/tanpa teks
MAX_TEXT = 12000         # potong teks panjang sebelum kirim ke AI
SLEEP_BETWEEN = 4.5      # detik antar panggilan (free tier ~15 req/menit)

PROMPT_DOC = (
    "Kamu asisten yang meringkas dokumen Keterbukaan Informasi Bursa Efek Indonesia. "
    "Ringkas isi dokumen berikut dalam 2-4 kalimat bahasa Indonesia yang jelas dan padat, "
    "fokus pada inti peristiwa dan dampak/relevansinya bagi investor. "
    "Jangan mengarang; hanya berdasarkan teks. Jangan sekadar mengulang judul.\n\n"
    "=== DOKUMEN ===\n"
)

PROMPT_COMPANY = (
    "Kamu meringkas aktivitas Keterbukaan Informasi sebuah emiten BEI berkode {code}. "
    "Di bawah ini ringkasan tiap pengumuman (kategori Aksi Korporasi & Laporan/Perjanjian). "
    "Buat SATU rangkuman menyeluruh 3-6 kalimat bahasa Indonesia yang MENYATUKAN poin-poin "
    "penting lintas pengumuman: tema utama, aksi korporasi/transaksi signifikan, dan hal "
    "yang relevan bagi investor. Sintesiskan, jangan sekadar menyalin. Jika hanya ada satu "
    "pengumuman, ringkas itu saja.\n\n=== RINGKASAN TIAP PENGUMUMAN ===\n{bullets}\n"
)


def load_key() -> str:
    if not KEY_FILE.exists():
        raise SystemExit(f"[error] File API key tidak ada: {KEY_FILE}\n"
                         "Ambil key gratis di https://aistudio.google.com/apikey lalu simpan di file itu.")
    key = KEY_FILE.read_text(encoding="utf-8").strip()
    if not key:
        raise SystemExit(f"[error] {KEY_FILE} kosong.")
    return key


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
            parts = cand.get("content", {}).get("parts", [])
            txt = "".join(p.get("text", "") for p in parts).strip()
            if not txt:
                raise RuntimeError(f"balasan kosong (finish={cand.get('finishReason')})")
            return txt
        except urllib.error.HTTPError as e:
            msg = e.read().decode("utf-8", "ignore")[:200]
            if e.code == 429 and attempt == 0:
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
    reader = PdfReader(io.BytesIO(base64.b64decode(res["data"])))
    return "\n".join((pg.extract_text() or "") for pg in reader.pages).strip()


def cat_of(a) -> str:
    return a.get("kategori") or classify(a.get("judul"), a.get("perihal"))


def summarize_docs(anns, key) -> int:
    """Tahap MAP: ringkasan per-dokumen untuk kategori aksi & lap."""
    targets = [a for a in anns
               if cat_of(a) in CATS_TO_SUMMARIZE
               and not a.get("summary")
               and a.get("summary_status") != "scan"
               and any(str(f.get("url", "")).lower().endswith(".pdf") for f in a.get("attachments", []))]
    targets = targets[:MAX_PER_RUN]
    if not targets:
        print("[map] tidak ada dokumen baru (aksi/lap) untuk diringkas.")
        return 0
    print(f"[map] {len(targets)} dokumen aksi/lap akan diringkas (maks {MAX_PER_RUN}).")

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
            label = f'{a.get("kode_emiten","?")} — {(a.get("judul") or "")[:45]}'
            try:
                text = fetch_pdf_text(page, pdf["url"])
            except Exception as e:
                print(f"[skip] {label}: {e}")
                continue
            if len(text) < MIN_TEXT:
                a["summary"] = None
                a["summary_status"] = "scan"
                print(f"[scan] {label}")
                continue
            try:
                a["summary"] = gemini(PROMPT_DOC + text[:MAX_TEXT], key)
                a["summary_status"] = "ok"
                done += 1
                print(f"[ok] {label}\n      -> {a['summary'][:110]}...")
            except Exception as e:
                print(f"[gemini-err] {label}: {e}")
                continue
            time.sleep(SLEEP_BETWEEN)
        ctx.close()
    return done


def summarize_companies(anns, company_summaries, key) -> int:
    """Tahap REDUCE: gabungkan ringkasan dok aksi/lap per emiten jadi 1 rangkuman."""
    groups = defaultdict(list)
    for a in anns:
        if cat_of(a) in CATS_TO_SUMMARIZE and a.get("summary"):
            code = (a.get("kode_emiten") or "").strip().upper()
            if code:
                groups[code].append(a)

    done = 0
    for code, rows in groups.items():
        rows.sort(key=lambda r: str(r.get("tanggal") or ""), reverse=True)
        sig = sorted([r.get("id") for r in rows if r.get("id")])
        prev = company_summaries.get(code)
        if prev and prev.get("based_on") == sig:
            continue                      # kumpulan dokumen tak berubah -> lewati
        if done >= MAX_COMPANY:
            continue
        bullets = "\n".join(
            f"- [{(r.get('tanggal') or '')[:10]}] {r.get('judul','')}: {r.get('summary','')}"
            for r in rows)
        try:
            csum = gemini(PROMPT_COMPANY.format(code=code, bullets=bullets), key, max_tokens=600)
        except Exception as e:
            print(f"[company-err] {code}: {e}")
            continue
        company_summaries[code] = {
            "summary": csum, "based_on": sig, "doc_count": len(rows),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        done += 1
        print(f"[company] {code} ({len(rows)} dok) -> {csum[:100]}...")
        time.sleep(SLEEP_BETWEEN)
    return done


def main() -> int:
    key = load_key()
    data = json.loads(OUT_FILE.read_text(encoding="utf-8"))
    anns = data.get("announcements", [])
    company_summaries = data.get("company_summaries", {})

    doc_done = summarize_docs(anns, key)
    comp_done = summarize_companies(anns, company_summaries, key)

    data["company_summaries"] = company_summaries
    OUT_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[done] {doc_done} ringkasan dokumen + {comp_done} ringkasan perusahaan disimpan.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
