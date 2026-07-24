# Backup pagi: scrape keterbukaan informasi IDX -> commit -> push ke GitHub.
# Dipanggil Task Scheduler 1x tiap pagi (07:00 WIB) sebagai jaring pengaman.
# Ringkasan TIDAK dibuat di sini (on-demand via app lokal / tombol Ringkas).
# Jalan di IP residensial supaya lolos Cloudflare.

# Catatan: git menulis info ke stderr (bukan error). Jadi JANGAN pakai
# ErrorActionPreference=Stop di sini -- cukup cek $LASTEXITCODE manual.
$ErrorActionPreference = "Continue"
$repo = "C:\Users\Pongo\idx-keterbukaan"
$py   = "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"
$log  = Join-Path $repo "run.log"

function Log($msg) {
  $line = "{0}  {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg
  Add-Content -Path $log -Value $line -Encoding utf8
  Write-Output $line
}

# Pastikan git tersedia (lokasi umum Git for Windows bila belum di PATH).
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
  $env:Path = "D:\Apps\Git\cmd;$env:Path"
}

Set-Location $repo
$env:IDX_HEADLESS = "0"   # headed (jendela di luar layar) -- paling andal lolos Cloudflare

Log "=== mulai ==="

# Sinkron dulu supaya tidak bentrok dengan commit lain.
& git pull --quiet --rebase --autostash origin main 2>&1 | Out-Null
Log "pull selesai (exit $LASTEXITCODE)"

# Jalankan scraper saja (ringkasan on-demand, bukan di sini).
& $py "fetcher/scrape.py" 2>&1 | ForEach-Object { Log "  $_" }
if ($LASTEXITCODE -ne 0) { Log "scraper gagal (exit $LASTEXITCODE)"; exit 1 }

# Commit hanya bila data berubah.
$changed = & git status --porcelain docs/announcements.json
if ([string]::IsNullOrWhiteSpace($changed)) {
  Log "tidak ada perubahan data."
} else {
  & git add docs/announcements.json 2>&1 | Out-Null
  & git commit --quiet -m ("data: update {0}" -f (Get-Date -Format "yyyy-MM-ddTHH:mmK")) 2>&1 | Out-Null
  & git push --quiet origin main 2>&1 | Out-Null
  if ($LASTEXITCODE -ne 0) { Log "push GAGAL (exit $LASTEXITCODE)"; exit 1 }
  Log "data ter-update & ter-push."
}

Log "=== selesai ==="
