@echo off
setlocal

cd /d "%~dp0"

echo ==================================================
echo  AI Router - Rebuild and Restart Docker Container
echo ==================================================
echo.

docker info >nul 2>&1
if errorlevel 1 (
    echo ERROR: Docker Desktop does not appear to be running.
    echo Please start Docker Desktop and try again.
    echo.
    pause
    exit /b 1
)

echo Rebuilding the image from the latest code and recreating the container...
echo ^(this picks up ANY change under backend/ or frontend/ - the image is
echo  rebuilt from scratch each time, not cached against old code^)
echo.

docker compose up --build --force-recreate -d

if errorlevel 1 (
    echo.
    echo ERROR: docker compose failed. See the output above for details.
    echo.
    pause
    exit /b 1
)

echo.
echo ==================================================
echo  Done. Open http://localhost:8000 in your browser.
echo  If it was already open, do a hard refresh:
echo  Ctrl+Shift+R  (or Ctrl+F5)
echo ==================================================
echo.
pause
