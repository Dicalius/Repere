"""Démarrage du conteneur (docker/entrypoint.py) face à un faux GitHub local : téléchargement de la branche
« valide », jeton jamais transmis à un autre hôte, repli sur la version précédente, archive malveillante,
dépendances modifiées, nettoyage. Aucun accès réseau extérieur."""
import importlib.util
import io
import json
import os
import subprocess
import sys
import tarfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("repere_entrypoint", ROOT / "docker" / "entrypoint.py")
entrypoint = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = entrypoint             # requis par @dataclass
SPEC.loader.exec_module(entrypoint)

SHA1, SHA2, SHA3 = "1" * 40, "2" * 40, "3" * 40


def tarball(sha, extra=None, broken=False):
    """Archive au format GitHub : tout est préfixé par « <dépôt>-<commit>/ »."""
    top = f"Dicalius-Repere-{sha[:7]}/"
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for folder in ("app", "config"):
            for path in (ROOT / folder).rglob("*"):
                if path.is_file() and "__pycache__" not in path.parts:
                    archive.add(path, top + str(path.relative_to(ROOT)))
        archive.add(ROOT / "requirements.txt", top + "requirements.txt")
        archive.add(ROOT / "README.md", top + "tests/ignore.md")            # hors des dossiers retenus
        for name, content in (extra or {}).items():
            info = tarfile.TarInfo(top + name)
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
        if broken:
            info = tarfile.TarInfo(top + "app/main.py")
            data = b"def casse(:\n"
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


class FakeGitHub:
    def __init__(self):
        self.commit = SHA1
        self.status = 200
        self.archives = {}
        self.requests = []
        github = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                github.requests.append((self.headers.get("Host"), self.path, self.headers.get("Authorization")))
                if self.path.startswith("/repos/o/r/commits/"):
                    if github.status != 200:
                        self.send_response(github.status)
                        self.end_headers()
                        return
                    body = github.commit.encode()
                elif self.path.startswith("/repos/o/r/tarball/"):
                    sha = self.path.rsplit("/", 1)[1]
                    # Comme api.github.com → codeload.github.com : redirection vers un autre hôte.
                    self.send_response(302)
                    self.send_header("Location", f"http://localhost:{github.port}/codeload/{sha}.tar.gz")
                    self.end_headers()
                    return
                elif self.path.startswith("/codeload/"):
                    body = github.archives[self.path.split("/")[-1].split(".")[0]]
                else:
                    self.send_response(404)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def downloads(self):
        return [path for _, path, _ in self.requests if path.startswith("/codeload/")]


@pytest.fixture
def github():
    fake = FakeGitHub()
    fake.archives = {SHA1: tarball(SHA1), SHA2: tarball(SHA2), SHA3: tarball(SHA3)}
    yield fake
    fake.server.shutdown()


def config(tmp_path, github, token=""):
    return entrypoint.Config(baked=ROOT, data=tmp_path / "data", repo="o/r", ref="valide", token=token,
                             api=f"http://127.0.0.1:{github.port}")


def test_premier_demarrage_telecharge_la_version_validee(tmp_path, github):
    state = entrypoint.prepare(config(tmp_path, github, token="jeton-secret"))
    assert (state["source"], state["commit"], state["ref"], state["python"]) == ("github", SHA1, "valide", sys.executable)
    code = Path(state["code_dir"])
    assert code == tmp_path / "data" / "releases" / SHA1
    assert (code / "app" / "main.py").is_file() and (code / "requirements.txt").is_file()
    assert not (code / "tests").exists()                                       # seuls app/, config/… sont gardés
    assert json.loads((tmp_path / "data" / "release.json").read_text())["commit"] == SHA1
    # Le jeton va à l'API, jamais à l'hôte de téléchargement (redirection).
    sent = {(host.split(":")[0], path.split("/")[1]): auth for host, path, auth in github.requests}
    assert sent[("127.0.0.1", "repos")] == "Bearer jeton-secret"
    assert sent[("localhost", "codeload")] is None


def test_meme_commit_rien_n_est_telecharge(tmp_path, github):
    cfg = config(tmp_path, github)
    entrypoint.prepare(cfg)
    entrypoint.prepare(cfg)
    assert len(github.downloads()) == 1


def test_code_qui_ne_se_charge_pas_garde_la_version_precedente(tmp_path, github, capsys):
    cfg = config(tmp_path, github)
    entrypoint.prepare(cfg)
    github.commit, github.archives[SHA2] = SHA2, tarball(SHA2, broken=True)
    state = entrypoint.prepare(cfg)
    assert state["commit"] == SHA1
    assert "ne se charge pas" in capsys.readouterr().out


def test_github_injoignable_relance_la_derniere_version_puis_l_image(tmp_path, github):
    cfg = config(tmp_path, github)
    entrypoint.prepare(cfg)
    offline = entrypoint.Config(**{**cfg.__dict__, "api": "http://127.0.0.1:9"})
    assert entrypoint.prepare(offline)["commit"] == SHA1
    fresh = entrypoint.Config(**{**offline.__dict__, "data": tmp_path / "vide"})
    state = entrypoint.prepare(fresh)
    assert (state["source"], state["code_dir"]) == ("image", str(ROOT))


def test_branche_valide_absente(tmp_path, github, capsys):
    github.status = 422
    state = entrypoint.prepare(config(tmp_path, github))
    assert state["source"] == "image" and "introuvable" in capsys.readouterr().out


def test_archive_malveillante_refusee(tmp_path, github, capsys):
    github.archives[SHA1] = tarball(SHA1, extra={"app/../../../evade.txt": b"x"})
    state = entrypoint.prepare(config(tmp_path, github))
    assert state["source"] == "image" and "refusée" in capsys.readouterr().out
    assert not any(p.name == "evade.txt" for p in tmp_path.rglob("*"))


def test_nettoyage_garde_la_version_lancee_et_la_precedente(tmp_path, github):
    cfg = config(tmp_path, github)
    for sha in (SHA1, SHA2, SHA3):
        github.commit = sha
        entrypoint.prepare(cfg)
    assert sorted(p.name for p in (tmp_path / "data" / "releases").iterdir()) == [SHA2, SHA3]


def test_dependances_modifiees_installees_une_fois(tmp_path, monkeypatch):
    code = tmp_path / "code"
    code.mkdir()
    (code / "requirements.txt").write_text((ROOT / "requirements.txt").read_text() + "nouvelle-dependance==1.0\n")
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        if "venv" in args:
            (Path(args[-1]) / "bin").mkdir(parents=True)
            (Path(args[-1]) / "bin" / "python").write_text("")
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(entrypoint.subprocess, "run", fake_run)
    cfg = entrypoint.Config(baked=ROOT, data=tmp_path / "data", repo="o/r", ref="valide", token="", api="")
    python = entrypoint.python_for(cfg, code)
    assert python.endswith("/bin/python") and "/venvs/" in python
    assert "--system-site-packages" in calls[0] and calls[1][-1] == str(code / "requirements.txt")
    assert entrypoint.python_for(cfg, code) == python and len(calls) == 2          # pas de seconde installation
    # Un simple commentaire ou un réordonnancement ne déclenche rien.
    (code / "requirements.txt").write_text("# commentaire\n" + "\n".join(reversed((ROOT / "requirements.txt").read_text().splitlines())))
    assert entrypoint.python_for(cfg, code) == sys.executable


def test_commande_repere(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "release.json").write_text(json.dumps({"source": "github", "commit": SHA1, "ref": "valide", "version": "9.9",
                                                   "code_dir": str(ROOT), "python": sys.executable, "started_at": "x"}))
    env = {**os.environ, "REPERE_DATA_DIR": str(data), "REPERE_TOKEN": "code-de-test"}
    run = lambda *args: subprocess.run([sys.executable, str(ROOT / "docker" / "repere"), *args], env=env,
                                       capture_output=True, text=True, timeout=60).stdout.strip()
    assert run("version").startswith("Repère 9.9 · commit 1111111 · origine : GitHub, branche valide")
    assert run("token") == "code-de-test"
