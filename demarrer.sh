#!/bin/sh
set -eu
cd "$(dirname "$0")"
if [ ! -f .env ]; then
  repere_password=$(od -An -N32 -tx1 /dev/urandom | tr -d ' \n')
  umask 077
  printf 'POSTGRES_PASSWORD=%s\nGEMINI_API_KEY=\nGEMINI_MODEL=gemini-3.5-flash-lite\n' "$repere_password" > .env
fi
docker compose up -d --wait --wait-timeout 180 || {
  echo "Image indisponible : construction locale."
  docker compose -f compose.yaml -f compose.build.yaml up -d --build --wait --wait-timeout 300
}
printf '\nCode de connexion :\n'
docker compose exec -T app python -m app.manage token
printf '\nOuvrir http://127.0.0.1:8765\n'
