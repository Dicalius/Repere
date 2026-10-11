@echo off
setlocal
cd /d "%~dp0"
docker info >nul 2>&1
if errorlevel 1 (
  echo Ouvre Docker Desktop puis relance ce fichier.
  pause
  exit /b 1
)

rem 1. Code, scripts et extension depuis GitHub (la base et .env ne sont pas concernes).
if not exist ".git" (
  echo Ce dossier n'est pas encore relie au depot GitHub. Une seule fois, dans ce dossier :
  echo   git init -b main
  echo   git remote add origin https://github.com/Dicalius/Repere.git
  echo   git fetch origin main
  echo   git reset --hard origin/main
  echo Le fichier .env et les donnees Docker sont conserves. Relance ensuite ce fichier.
  pause
  exit /b 1
)
set BEFORE=
for /f %%h in ('git rev-parse HEAD 2^>nul') do set BEFORE=%%h
git pull --ff-only
if errorlevel 1 (
  echo.
  echo Mise a jour du dossier impossible. Des fichiers ont peut-etre ete modifies a la main :
  git status --short
  pause
  exit /b 1
)

rem 2. Nouvelle image de l'application (ghcr.io), sinon construction locale.
docker compose pull app
if errorlevel 1 (
  echo Image non telechargeable ^(connexion ou "docker login ghcr.io" absent^) : construction locale.
  docker compose -f compose.yaml -f compose.build.yaml up -d --build --wait --wait-timeout 300
) else (
  docker compose up -d --wait --wait-timeout 180
)
if errorlevel 1 (
  echo Le redemarrage a echoue.
  docker compose logs --tail 40 app
  pause
  exit /b 1
)

echo.
docker compose exec -T app python -c "from app import VERSION; print('Repere ' + VERSION + ' est a jour.')"
if defined BEFORE (
  git diff --quiet %BEFORE% HEAD -- extension
  if errorlevel 1 (
    echo.
    echo L'EXTENSION A CHANGE : ouvre chrome://extensions et clique sur Recharger sous Repere.
  )
)
pause
