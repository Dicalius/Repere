@echo off
cd /d "%~dp0"
docker info >nul 2>&1
if errorlevel 1 (
  echo Ouvre Docker Desktop puis relance ce fichier.
  pause
  exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0INITIALISER.ps1"
if errorlevel 1 (
  pause
  exit /b 1
)
rem Image publiee sur ghcr.io (telechargee si absente), sinon construction locale.
docker compose up -d --wait --wait-timeout 180
if errorlevel 1 (
  echo Image indisponible : construction locale, le premier lancement prend quelques minutes.
  docker compose -f compose.yaml -f compose.build.yaml up -d --build --wait --wait-timeout 300
)
if errorlevel 1 (
  echo Le demarrage a echoue. Verifie que l'ancien serveur Excel est arrete.
  docker compose logs --tail 30 app
  pause
  exit /b 1
)
echo.
echo CODE DE CONNEXION - a copier dans Repere et dans l'extension :
docker compose exec -T app python -m app.manage token
echo.
start "" "http://127.0.0.1:8765"
pause
