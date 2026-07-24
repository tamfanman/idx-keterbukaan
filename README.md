# Keterbukaan Informasi BEI — Auto Update (Gratis)

Platform yang menarik data **Keterbukaan Informasi** dari [IDX/BEI](https://www.idx.co.id/id/perusahaan-tercatat/keterbukaan-informasi/)
secara otomatis **setiap 2 jam**, lalu menampilkannya di halaman web.

**100% gratis** — jalan di GitHub Actions (scheduler + scraper) dan GitHub Pages (frontend). Tanpa server, tanpa biaya.

## Kenapa pakai Playwright (bukan requests biasa)?
Situs IDX dilindungi **Cloudflare Bot Management**. HTTP request biasa selalu kena `403 Forbidden`.
Playwright menjalankan browser Chromium sungguhan sehingga lolos challenge Cloudflare, lalu
menangkap response API `GetAnnouncement` yang di-fetch oleh halaman itu sendiri.

## Struktur
```
.github/workflows/fetch.yml   # scheduler cron tiap 2 jam + commit hasil
fetcher/scrape.py             # scraper Playwright (capture response API IDX)
fetcher/requirements.txt      # dependency Python
docs/index.html               # frontend (GitHub Pages)
docs/announcements.json       # hasil data (di-commit tiap run)
```

## Cara setup (sekali saja)

1. **Buat repo GitHub** (boleh private atau public) lalu push isi folder ini:
   ```bash
   git init
   git add .
   git commit -m "init: platform keterbukaan informasi BEI"
   git branch -M main
   git remote add origin https://github.com/<username>/<repo>.git
   git push -u origin main
   ```

2. **Aktifkan GitHub Pages**: Settings → Pages → Source = `Deploy from a branch`,
   Branch = `main`, Folder = `/docs`. Simpan. URL situs muncul di situ.

3. **Tes scraper**: tab **Actions** → workflow *Fetch Keterbukaan Informasi IDX* →
   **Run workflow**. Tunggu selesai, lihat log.
   - ✅ Kalau hijau & `docs/announcements.json` terisi → berhasil.
   - ⚠️ Kalau gagal capture, cek artifact/commit `docs/_raw_last_response.json`
     dan screenshot debug untuk lihat struktur asli / blokir Cloudflare.

4. Setelah itu berjalan **otomatis tiap 2 jam**. Tidak perlu diapa-apakan lagi.

## Catatan
- Cron GitHub Actions kadang telat beberapa menit saat runner sibuk — wajar.
- Repo **private yang idle 60 hari** akan menonaktifkan scheduled workflow; karena ini
  jalan tiap 2 jam, tidak akan kena.
- File pertama `docs/_raw_last_response.json` sangat berguna untuk memverifikasi/menyesuaikan
  parsing di `fetcher/scrape.py` bila struktur field IDX berbeda dari perkiraan.
- Patuhi Terms of Use IDX. Frekuensi 2 jam tergolong wajar untuk pemakaian pribadi/internal.
