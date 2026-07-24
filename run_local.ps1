# Runner lokal: ambil keterbukaan informasi IDX -> commit -> push ke GitHub.
# Dipanggil otomatis oleh Windows Task Scheduler tiap 2 jam (jam kerja).
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

# Jalankan scraper.
& $py "fetcher/scrape.py" 2>&1 | ForEach-Object { Log "  $_" }
if ($LASTEXITCODE -ne 0) { Log "scraper gagal (exit $LASTEXITCODE)"; exit 1 }

# Ringkas dokumen baru (Gemini). Kalau gagal (mis. kuota harian habis), JANGAN
# gagalkan seluruh run -- data pengumuman tetap di-commit, ringkasan menyusul nanti.
& $py "fetcher/summarize.py" 2>&1 | ForEach-Object { Log "  $_" }
if ($LASTEXITCODE -ne 0) { Log "summarize gagal (exit $LASTEXITCODE) - dilanjut tanpa ringkasan baru" }

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
