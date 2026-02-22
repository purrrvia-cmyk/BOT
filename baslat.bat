@echo off
chcp 65001 >nul
title ICT Trading Bot
echo ============================================
echo   ICT Trading Bot - Başlatılıyor
echo ============================================
echo.

cd /d "%~dp0"

REM Python kontrolü
python --version >nul 2>&1
if errorlevel 1 (
    echo [HATA] Python bulunamadı! Python 3.8+ yükleyin.
    echo https://www.python.org/downloads/
    pause
    exit /b 1
)

REM Sanal ortam oluştur (ilk çalıştırmada)
if not exist "venv" (
    echo [*] Sanal ortam oluşturuluyor...
    python -m venv venv
)

REM Sanal ortamı aktifle
call venv\Scripts\activate.bat

REM Bağımlılıkları yükle
echo [*] Bağımlılıklar kontrol ediliyor...
pip install -r requirements.txt --quiet

echo.
echo ============================================
echo   Bot başlatıldı!
echo   Tarayıcıda açın: http://localhost:5000
echo   Durdurmak için: Ctrl+C
echo ============================================
echo.

python app.py

pause
