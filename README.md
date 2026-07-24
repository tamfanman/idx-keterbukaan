# Keterbukaan Informasi BEI — Auto Update (Gratis)

Menarik data **Keterbukaan Informasi** dari [IDX/BEI](https://www.idx.co.id/id/perusahaan-tercatat/keterbukaan-informasi/)
otomatis **tiap 2 jam (07:00–21:00 WIB)** dan menampilkannya di web. **100% gratis.**

🌐 **Situs:** https://tamfanman.github.io/idx-keterbukaan/

## Arsitektur

Situs IDX dilindungi **Cloudflare Bot Management** yang **memblokir semua IP datacenter**
(GitHub Actions, cloud gratis, dll). Jadi bagian pengambilan data **harus jalan dari IP
residensial** — yaitu komputer ini. Frontend tetap gratis 24/7 di GitHub Pages.

```
PC ini (Task Scheduler, tiap 2 jam, 07-21 WIB)
   └─ run_local.ps1
        ├─ git pull
        ├─ python fetcher/scrape.py   → patchright + Chrome (headed, jendela di luar layar)
        │                               lolos Cloudflare → panggil API GetAnnouncement
        │                               → docs/announcements.json
        └─ git commit + push → GitHub
GitHub Pages (docs/) → tampilkan data 24/7  (GRATIS, selalu online)
```

## File
```
fetcher/scrape.py        # scraper: patchright, lolos Cloudflare, ambil + normalisasi data
fetcher/requirements.txt # dependency (patchright)
run_local.ps1            # dipanggil Task Scheduler: pull → scrape → commit → push
docs/index.html          # frontend (GitHub Pages): daftar pengumuman + cari + link PDF
docs/announcements.json  # data (di-commit tiap update)
run.log                  # log tiap run (tidak di-commit)
```

## Cara kerja & perawatan

- **Jadwal**: Windows Task Scheduler, task **"IDX Keterbukaan Informasi"**, tiap 2 jam
  antara 07:00–21:00. Jalan hanya saat user login. Kalau PC mati, siklus terlewat &
  lanjut saat nyala lagi.
- **Mode browser**: headed dengan jendela ditaruh di luar layar (`--window-position=-2400,-2400`)
  supaya andal lolos Cloudflare tapi tidak mengganggu. Set env `IDX_HEADLESS=1` untuk debug
  headless (kurang andal).
- **Endpoint data**: `www.idx.co.id/primary/ListedCompany/GetAnnouncement` (JSON).
  Field unik = `Id2` (bukan `Id` yang selalu 0).

### Perintah berguna (PowerShell)
```powershell
# Jalankan manual sekarang
schtasks /Run /TN "IDX Keterbukaan Informasi"

# Lihat status / next run
schtasks /Query /TN "IDX Keterbukaan Informasi" /FO LIST

# Lihat log terakhir
Get-Content run.log -Tail 20

# Nonaktifkan / aktifkan
schtasks /Change /TN "IDX Keterbukaan Informasi" /DISABLE
schtasks /Change /TN "IDX Keterbukaan Informasi" /ENABLE

# Jalankan scraper langsung (tanpa commit)
& "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe" fetcher/scrape.py
```

## Catatan
- Prasyarat (sudah terpasang): Python 3.12, `pip install patchright`, `patchright install chrome`, Git, gh (login).
- Patuhi Terms of Use IDX; frekuensi 2 jam pada jam kerja tergolong wajar untuk pemakaian pribadi.
