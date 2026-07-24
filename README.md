# Keterbukaan Informasi BEI — App Lokal + Situs Publik (Gratis)

Menarik data **Keterbukaan Informasi** [IDX/BEI](https://www.idx.co.id/id/perusahaan-tercatat/keterbukaan-informasi/),
mengkategorikan, dan **meringkas** pengumuman Aksi Korporasi pakai AI gratis. **100% gratis.**

- 🖥️ **App lokal** (di PC ini): tombol **Refresh** (ambil data terbaru) & **Ringkas**
  (buat rangkuman perusahaan on-demand). Dobel-klik **`start-app.bat`** → buka `http://localhost:8080`.
- 🌐 **Situs publik** (read-only): https://tamfanman.github.io/idx-keterbukaan/ — penampil
  data terbaru, tanpa tombol (di-update tiap kali app lokal Refresh/Ringkas + backup pagi).

## Kenapa harus jalan di PC ini?

Situs IDX dilindungi **Cloudflare Bot Management** yang **memblokir semua IP datacenter**
(GitHub Actions & cloud gratis → 403). Hanya **IP residensial** (PC ini) yang lolos. Karena
itu scraping & unduh PDF **harus** di sini; hasilnya di-push ke GitHub Pages (penampil publik).

## Alur

```
App lokal (fetcher/server.py, http://localhost:8080)
  ├─ tombol Refresh  → scrape.run()            → data terbaru → git push
  └─ tombol Ringkas  → summarize_company(code) → unduh PDF Aksi Korporasi emiten itu,
                        ekstrak teks, ringkas via Gemini, buat rangkuman perusahaan → git push
Backup pagi (Task Scheduler 07:00 WIB) → run_local.ps1 → scrape saja → git push
GitHub Pages (docs/) → penampil publik read-only
```

## Kategori & ringkasan
- Kategori (gratis, dari judul, `fetcher/categorize.py`): **Aksi Korporasi**, **Laporan &
  Perjanjian**, **Not Sure** (mungkin penting), **Spam** (rutin).
- **Ringkasan AI** (Gemini free tier) **hanya untuk Aksi Korporasi** — per-dokumen + rangkuman
  level-perusahaan. On-demand saat klik **Ringkas**. Butuh `.gemini_key` (root repo, tak di-commit;
  ambil di https://aistudio.google.com/apikey). PDF scan/gambar ditandai (tak bisa diringkas).

## File
```
fetcher/server.py        # app lokal (Flask): serve UI + /api/refresh + /api/summarize
fetcher/scrape.py        # scrape (patchright, lolos Cloudflare) — fungsi run()
fetcher/summarize.py     # ringkasan Gemini — summarize_company(code) on-demand
fetcher/categorize.py    # klasifikasi kategori (aksi/lap/notsure/spam)
fetcher/requirements.txt # patchright, pypdf, flask
start-app.bat            # dobel-klik untuk menjalankan app lokal
run_local.ps1            # backup pagi: scrape → push (dipanggil Task Scheduler)
docs/index.html          # UI (dipakai app lokal & situs publik; deteksi backend via /api/health)
docs/announcements.json  # data + company_summaries
```

## Perintah berguna (PowerShell)
```powershell
$py = "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"
& $py fetcher/server.py            # jalankan app lokal manual
& $py fetcher/scrape.py            # scrape sekali (tanpa push)
& $py fetcher/summarize.py         # ringkas batch semua Aksi Korporasi (manual)
schtasks /Run   /TN "IDX Keterbukaan Informasi"    # jalankan backup pagi sekarang
schtasks /Query /TN "IDX Keterbukaan Informasi" /FO LIST
```

## Catatan
- Prasyarat (terpasang): Python 3.12 + `patchright`/`pypdf`/`flask`, `patchright install chrome`, Git, gh (login).
- App lokal bind ke `127.0.0.1` (hanya PC ini). Backup pagi & tombol butuh user login + PC nyala.
- Patuhi Terms of Use IDX; pemakaian pribadi frekuensi wajar.
