# Runner lokal: ambil keterbukaan informasi IDX -> commit -> push ke GitHub.
# Dipanggil otomatis oleh Windows Task Scheduler tiap 2 jam (jam kerja).
# Jalan di IP residensial supaya lolos Cloudflare.

$ErrorActionPreference = "Stop"
$repo = "C:\Users\Pongo\idx-keterbukaan"
$py   = "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"
$log  = Join-Path $repo "run.log"

function Log($msg) {
  $line = "{0}  {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg
  Add-Content -Path $log -Value $line -Encoding utf8
  Write-Output $line
}

# Pastikan git ada di PATH (lokasi umum Git for Windows).
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
  $env:Path = "D:\Apps\Git\cmd;$env:Path"
}

Set-Location $repo
$env:IDX_HEADLESS = "1"   # tanpa jendela popup

try {
  Log "=== mulai ==="

  # Sinkron dulu supaya tidak bentrok dengan commit lain.
  git pull --rebase --autostash origin main 2>&1 | Out-Null

  # Jalankan scraper.
  & $py "fetcher/scrape.py" 2>&1 | ForEach-Object { Log "  $_" }
  if ($LASTEXITCODE -ne 0) { Log "scraper gagal (exit $LASTEXITCODE)"; exit 1 }

  # Commit hanya bila data berubah.
  $changed = git status --porcelain docs/announcements.json
  if ([string]::IsNullOrWhiteSpace($changed)) {
    Log "tidak ada perubahan data."
  } else {
    git add docs/announcements.json
    git commit -m ("data: update {0}" -f (Get-Date -Format "yyyy-MM-ddTHH:mmK")) 2>&1 | Out-Null
    git push origin main 2>&1 | Out-Null
    Log "data ter-update & ter-push."
  }
  Log "=== selesai ==="
}
catch {
  Log ("ERROR: " + $_.Exception.Message)
  exit 1
}
