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
import os
import re
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

# patchright = fork Playwright dengan patch anti-deteksi (lolos Cloudflare).
# API-nya identik dengan playwright biasa.
from patchright.sync_api import sync_playwright

# --- Konfigurasi ----------------------------------------------------------
KETERBUKAAN_URL = "https://www.idx.co.id/id/perusahaan-tercatat/keterbukaan-informasi/"
# Cocokkan beberapa kemungkinan nama endpoint pengumuman IDX (case-insensitive).
API_PATTERNS = ["announcement", "pengumuman", "disclosure", "keterbukaan"]

# Kandidat URL API pengumuman untuk dipanggil langsung (variasi nama parameter).
API_CANDIDATES = [
    "/primary/ListedCompany/GetAnnouncement?indexFrom=1&pageSize=50&dateFrom=&dateTo=&lang=id&keyword=&emitenType=*",
    "/primary/ListedCompany/GetAnnouncement?indexFrom=0&pageSize=50&lang=id",
    "/primary/ListedCompany/GetAnnouncement?pageNumber=1&pageSize=50&lang=id",
]
DOCS = Path(__file__).resolve().parent.parent / "docs"
OUT_FILE = DOCS / "announcements.json"
RAW_DEBUG = DOCS / "_raw_last_response.json"
URLS_DEBUG = DOCS / "_debug_urls.json"      # daftar SEMUA URL yang di-fetch halaman
HTML_DEBUG = DOCS / "_debug_page.html"      # snapshot HTML (deteksi challenge Cloudflare)
NAV_TIMEOUT_MS = 60_000
CAPTURE_WAIT_S = 35                          # tunggu maksimal sekian detik untuk API muncul

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
                if isinstance(meta, dict):
                    v = meta.get(k)
                    if isinstance(v, str):
                        v = v.strip()
                    if v not in (None, ""):
                        return v
            return None

        records.append({
            # Id selalu 0 di respons IDX; Id2 / NoPengumuman yang unik.
            "id": pick("Id2", "NoPengumuman", "No_Pengumuman"),
            "kode_emiten": pick("Kode_Emiten", "KodeEmiten", "Emiten"),
            "judul": pick("JudulPengumuman", "Judul", "Title"),
            "perihal": pick("PerihalPengumuman", "Perihal"),
            "tanggal": pick("TglPengumuman", "TanggalPengumuman", "Tanggal", "PublishDate"),
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


def looks_like_api(url: str) -> bool:
    u = url.lower()
    return any(p in u for p in API_PATTERNS)


def capture() -> dict | None:
    captured = {"payload": None, "hit_url": None}
    seen = []   # semua response (url, status, content-type) untuk diagnosa

    with sync_playwright() as p:
        # Best-practice patchright agar lolos Cloudflare:
        #  - channel="chrome" (Chrome asli, bukan Chromium)
        #  - headless=False (dijalankan di bawah xvfb pada CI)
        #  - JANGAN override user_agent/viewport (bisa merusak stealth)
        #  - persistent context (profil nyata)
        profile_dir = str(Path(tempfile.gettempdir()) / "pw-idx-profile")
        # Headless bikin tanpa jendela popup; headed lebih andal lolos Cloudflare.
        # Atur lewat env IDX_HEADLESS=1 (default: headed).
        headless = os.environ.get("IDX_HEADLESS", "0") == "1"
        print(f"[cfg] headless={headless}")
        context = p.chromium.launch_persistent_context(
            user_data_dir=profile_dir,
            channel="chrome",
            headless=headless,
            no_viewport=True,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
        )
        page = context.new_page()

        def on_response(resp):
            ct = ""
            try:
                ct = resp.headers.get("content-type", "")
            except Exception:
                pass
            seen.append({"url": resp.url, "status": resp.status, "content_type": ct})
            # Kandidat: URL yang namanya cocok pola pengumuman DAN balikannya JSON.
            if captured["payload"] is None and looks_like_api(resp.url) and "json" in ct.lower():
                try:
                    data = resp.json()
                    captured["payload"] = data
                    captured["hit_url"] = resp.url
                    print(f"[capture] API tertangkap: {resp.url}")
                except Exception as e:
                    print(f"[capture] cocok pola tapi gagal parse JSON dari {resp.url}: {e}")

        page.on("response", on_response)

        print(f"[nav] membuka {KETERBUKAAN_URL} ...")
        page.goto(KETERBUKAAN_URL, timeout=NAV_TIMEOUT_MS, wait_until="domcontentloaded")

        def on_challenge(t):
            return any(m in t for m in ("Just a moment", "Attention Required", "Cloudflare"))

        def try_click_turnstile():
            # Turnstile ada di dalam iframe; coba klik checkbox-nya bila muncul.
            for fr in page.frames:
                if "challenges.cloudflare.com" in (fr.url or ""):
                    for sel in ("input[type=checkbox]", "label", "body"):
                        try:
                            el = fr.query_selector(sel)
                            if el:
                                el.click(timeout=2000)
                                print(f"[cf] klik Turnstile ({sel})")
                                return
                        except Exception:
                            pass

        def try_api() -> bool:
            """Panggil API pengumuman langsung dari konteks halaman (same-origin,
            cookie ikut). Sering berhasil walau challenge visual belum kelar karena
            cookie __cf_bm dari navigasi awal sudah cukup untuk XHR."""
            for api in API_CANDIDATES:
                try:
                    res = page.evaluate(
                        """async (u) => {
                            const r = await fetch(u, {headers:{'Accept':'application/json'}, credentials:'include'});
                            return {status: r.status, body: await r.text()};
                        }""",
                        api,
                    )
                except Exception as e:
                    print(f"[api] gagal fetch {api}: {e}")
                    continue
                print(f"[api] {res['status']} <- {api}")
                if res["status"] == 200:
                    try:
                        captured["payload"] = json.loads(res["body"])
                        captured["hit_url"] = api
                        print(f"[api] JSON OK dari {api}")
                        return True
                    except Exception as e:
                        print(f"[api] 200 tapi bukan JSON ({e}); cuplikan: {res['body'][:160]}")
                else:
                    print(f"[api] cuplikan body: {res['body'][:160]}")
            return False

        # Beri halaman waktu settle sebentar, lalu coba API DULUAN.
        try:
            page.wait_for_load_state("networkidle", timeout=8_000)
        except Exception:
            pass

        # Kalau API langsung tembus, tak perlu menunggu challenge sama sekali (cepat,
        # cocok untuk mode headless). Kalau belum, baru coba selesaikan challenge.
        if not try_api():
            print("[cf] API belum tembus, coba selesaikan challenge...")
            cf_deadline = time.time() + 90
            reloaded = False
            while time.time() < cf_deadline and captured["payload"] is None:
                try:
                    t = page.title()
                except Exception:
                    t = ""
                if not on_challenge(t):
                    print(f"[cf] challenge lewat. judul: {t!r}")
                    if try_api():
                        break
                try_click_turnstile()
                if not reloaded and time.time() > cf_deadline - 55:
                    print("[cf] masih nyangkut, reload sekali...")
                    try:
                        page.reload(timeout=NAV_TIMEOUT_MS, wait_until="domcontentloaded")
                    except Exception:
                        pass
                    reloaded = True
                page.wait_for_timeout(3000)
                if captured["payload"] is None:
                    try_api()

        # Selalu simpan artefak diagnosa (berguna baik sukses maupun gagal).
        try:
            URLS_DEBUG.write_text(json.dumps(seen, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"[debug] {len(seen)} URL tercatat -> {URLS_DEBUG.name}")
        except Exception:
            pass
        if captured["payload"] is None:
            try:
                page.screenshot(path=str(RAW_DEBUG.with_suffix(".png")), full_page=True)
                HTML_DEBUG.write_text(page.content(), encoding="utf-8")
                title = page.title()
                print(f"[debug] gagal capture. judul halaman: {title!r}")
                print("[debug] URL kandidat (cocok pola) yang terlihat:")
                for s in seen:
                    if looks_like_api(s["url"]):
                        print(f"        - [{s['status']}] {s['content_type']} {s['url']}")
            except Exception as e:
                print(f"[debug] gagal ambil screenshot/html: {e}")

        context.close()

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
