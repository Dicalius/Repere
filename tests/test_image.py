"""Image Docker : commit d'origine (REPERE_COMMIT, fourni par GitHub Actions) affiché par « repere version »
et dans /api/status ; Dockerfile sans téléchargement au démarrage."""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SHA = "0123456789abcdef0123456789abcdef01234567"


def manage(*args, commit=None):
    env = {k: v for k, v in os.environ.items() if k != "REPERE_COMMIT"}
    if commit:
        env["REPERE_COMMIT"] = commit
    return subprocess.run([sys.executable, "-m", "app.manage", *args], cwd=ROOT, env=env,
                          capture_output=True, text=True, timeout=60).stdout.strip()


def test_repere_version_affiche_le_commit_de_l_image():
    from app import VERSION
    assert manage("version", commit=SHA) == f"Repère {VERSION} · commit 0123456"
    assert manage("version") == f"Repère {VERSION} · image construite localement"
    assert manage("token") == "repere-test-token"


def test_statut_indique_le_commit(client, monkeypatch):
    assert client.get("/api/status").json()["commit"] is None
    monkeypatch.setenv("REPERE_COMMIT", SHA)
    assert client.get("/api/status").json()["commit"] == SHA


def test_image_lance_directement_l_application():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert 'CMD ["uvicorn", "app.main:app"' in dockerfile and "ENTRYPOINT" not in dockerfile
    assert "ARG REPERE_COMMIT" in dockerfile
    # Le commit change à chaque build : il doit venir après l'installation des dépendances (cache).
    assert dockerfile.index("pip install") < dockerfile.index("ARG REPERE_COMMIT")
    assert (ROOT / "docker" / "repere").read_text(encoding="utf-8").startswith("#!/bin/sh\n")
