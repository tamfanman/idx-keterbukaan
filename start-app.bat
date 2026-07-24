@echo off
REM ====== Jalankan app lokal IDX Keterbukaan Informasi ======
REM Dobel-klik file ini untuk membuka aplikasi.
cd /d "%~dp0"
set PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe
echo Menjalankan server IDX... jangan tutup jendela ini selama memakai app.
start "IDX server" "%PY%" "fetcher\server.py"
REM tunggu server siap lalu buka browser
timeout /t 4 /nobreak >nul
start "" http://localhost:8080
