"""
Scraper Keterbukaan Informasi BEI/IDX.

Strategi: IDX dilindungi Cloudflare Bot Management, jadi HTTP request biasa
selalu 403. Kita pakai Playwright (browser sungguhan) untuk lolos challenge
Cloudflare, lalu MENANGKAP response API GetAnnouncement yang di-fetch sendiri
oleh halaman (response interception) -- pendekatan ini paling tahan banting
karena kita tidak perlu menebak parameter API yang benar.

Output di-merge dengan data lama supaya riwayat pengumuman tidak hilang.
"""

import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright

# --- Konfigurasi ----------------------------------------------------------
KETERBUKAAN_URL = "https://www.idx.co.id/id/perusahaan-tercatat/keterbukaan-informasi/"
API_MATCH = "GetAnnouncement"           # substring yang menandai response API
OUT_FILE = Path(__file__).resolve().parent.parent / "docs" / "announcements.json"
RAW_DEBUG = Path(__file__).resolve().parent.parent / "docs" / "_raw_last_response.json"
NAV_TIMEOUT_MS = 60_000
CAPTURE_WAIT_S = 25                       # tunggu maksimal sekian detik untuk API muncul

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


def normalize(raw: dict) -> list[dict]:
    """Ubah response mentah IDX jadi daftar record ringkas & stabil.

    Struktur IDX bisa berubah, jadi kita cari list-nya secara defensif lalu
    ambil field yang paling mungkin ada. Field yang tak ketemu diisi None.
    """
    # IDX biasanya membungkus di 'Replies' atau 'Items'; cari list dict pertama.
    candidates = []
    if isinstance(raw, dict):
        for key in ("Replies", "Items", "items", "data", "Data"):
            if isinstance(raw.get(key), list):
                candidates = raw[key]
                break
    elif isinstance(raw, list):
        candidates = raw

    records = []
    for item in candidates:
        if not isinstance(item, dict):
            continue
        # Metadata pengumuman bisa langsung di item atau nested di 'pengumuman'.
        meta = item.get("pengumuman") if isinstance(item.get("pengumuman"), dict) else item
        attachments = item.get("attachments") or item.get("Attachments") or []
        files = []
        for att in attachments if isinstance(attachments, list) else []:
            if isinstance(att, dict):
                path = att.get("FullSavePath") or att.get("PathPengumuman") or att.get("Url")
                name = att.get("OriginalFilename") or att.get("FileName") or att.get("JenisAttachment")
                if path:
                    files.append({"name": name, "url": path})

        def pick(*keys):
            for k in keys:
                if isinstance(meta, dict) and meta.get(k) not in (None, ""):
                    return meta[k]
            return None

        records.append({
            "id": pick("Id", "id", "NoPengumuman", "No_Pengumuman"),
            "kode_emiten": pick("Kode_Emiten", "KodeEmiten", "Emiten"),
            "judul": pick("JudulPengumuman", "Judul", "Title", "Perihal"),
            "tanggal": pick("TanggalPengumuman", "Tanggal", "TglPengumuman", "PublishDate"),
            "jenis": pick("JenisPengumuman", "Kategori", "Category"),
            "attachments": files,
        })
    return records


def dedupe_key(rec: dict) -> str:
    if rec.get("id") is not None:
        return f"id::{rec['id']}"
    # fallback: gabungan emiten + judul + tanggal
    return f"{rec.get('kode_emiten')}|{rec.get('judul')}|{rec.get('tanggal')}"


def load_existing() -> list[dict]:
    if OUT_FILE.exists():
        try:
            data = json.loads(OUT_FILE.read_text(encoding="utf-8"))
            return data.get("announcements", []) if isinstance(data, dict) else []
        except Exception:
            return []
    return []


def capture() -> dict | None:
    captured = {"payload": None}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        context = browser.new_context(user_agent=UA, locale="id-ID")
        page = context.new_page()

        def on_response(resp):
            if API_MATCH in resp.url and resp.status == 200:
                try:
                    captured["payload"] = resp.json()
                    print(f"[capture] API tertangkap: {resp.url}")
                except Exception as e:
                    print(f"[capture] gagal parse JSON dari {resp.url}: {e}")

        page.on("response", on_response)

        print(f"[nav] membuka {KETERBUKAAN_URL} ...")
        page.goto(KETERBUKAAN_URL, timeout=NAV_TIMEOUT_MS, wait_until="domcontentloaded")

        # Tunggu Cloudflare + XHR halaman. Poll sampai payload muncul.
        deadline = time.time() + CAPTURE_WAIT_S
        while time.time() < deadline and captured["payload"] is None:
            page.wait_for_timeout(1000)

        # Simpan screenshot debug kalau gagal (berguna lihat blokir Cloudflare).
        if captured["payload"] is None:
            shot = RAW_DEBUG.with_suffix(".png")
            try:
                page.screenshot(path=str(shot))
                print(f"[debug] tidak ada API tertangkap; screenshot -> {shot}")
            except Exception:
                pass

        context.close()
        browser.close()

    return captured["payload"]


def main() -> int:
    payload = capture()
    if payload is None:
        print("[error] Tidak ada response GetAnnouncement tertangkap. "
              "Cek screenshot debug; mungkin struktur halaman berubah.")
        return 1

    # Simpan raw untuk inspeksi struktur asli (sekali jalan pertama sangat berguna).
    RAW_DEBUG.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    fresh = normalize(payload)
    print(f"[parse] {len(fresh)} record ter-normalisasi dari response.")

    # Merge dengan riwayat lama, dedupe.
    existing = load_existing()
    by_key = {dedupe_key(r): r for r in existing}
    added = 0
    for rec in fresh:
        k = dedupe_key(rec)
        if k not in by_key:
            added += 1
        by_key[k] = rec

    merged = list(by_key.values())
    # Urutkan terbaru dulu bila tanggal bisa dibandingkan sebagai string ISO-ish.
    merged.sort(key=lambda r: str(r.get("tanggal") or ""), reverse=True)

    out = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "count": len(merged),
        "source": KETERBUKAAN_URL,
        "announcements": merged,
    }
    OUT_FILE.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[done] +{added} baru, total {len(merged)} -> {OUT_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
