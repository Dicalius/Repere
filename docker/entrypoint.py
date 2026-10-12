"""Démarrage du conteneur de l'application Repère.

À chaque démarrage (ou redémarrage) du conteneur :
1. demande à GitHub le commit de la branche « valide », que GitHub Actions avance quand les tests réussissent
   sur main ; s'il a changé, télécharge ce code (app/, config/, requirements.txt) ;
2. installe les dépendances Python, une seule fois, si requirements.txt diffère de celui de l'image ;
3. vérifie que le nouveau code se charge ; sinon, garde la version précédente ;
4. lance l'application.

Sans accès à GitHub, la dernière version téléchargée est relancée, ou à défaut le code inclus dans l'image.
La base PostgreSQL n'est jamais touchée. Le code téléchargé est conservé dans le volume de données.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

IMAGE_DIR = Path(__file__).resolve().parent          # /opt/repere : fichiers de démarrage de l'image
KEPT_DIRS = ("app/", "config/", "docker/")
KEPT_FILES = ("requirements.txt", "Dockerfile")
MAX_ARCHIVE = 60_000_000


def log(message: str) -> None:
    print(f"[repère] {message}", flush=True)


@dataclass
class Config:
    baked: Path
    data: Path
    repo: str
    ref: str
    token: str
    api: str

    @classmethod
    def from_env(cls):
        repo = os.getenv("REPERE_GITHUB_REPO", "").strip()
        if repo and not re.fullmatch(r"[\w.-]+/[\w.-]+", repo):
            log(f"REPERE_GITHUB_REPO invalide ({repo!r}) : mise à jour désactivée.")
            repo = ""
        return cls(baked=Path(os.getenv("REPERE_BAKED_DIR", "/app")), data=Path(os.getenv("REPERE_DATA_DIR", "/app/data")),
                   repo=repo, ref=os.getenv("REPERE_GITHUB_REF", "").strip() or "valide",
                   token=os.getenv("REPERE_GITHUB_TOKEN", "").strip(),
                   api=os.getenv("REPERE_GITHUB_API", "https://api.github.com").rstrip("/"))

    @property
    def releases(self):
        return self.data / "releases"

    @property
    def venvs(self):
        return self.data / "venvs"

    @property
    def state_file(self):
        return self.data / "release.json"


class _Redirect(urllib.request.HTTPRedirectHandler):
    """Le jeton n'est jamais transmis à un autre hôte (codeload.github.com reçoit une URL signée)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None and urllib.parse.urlsplit(newurl).netloc != urllib.parse.urlsplit(req.full_url).netloc:
            new.remove_header("Authorization")
        return new


def fetch(cfg: Config, path: str, accept: str, timeout: float) -> bytes:
    headers = {"Accept": accept, "User-Agent": "repere-conteneur", "X-GitHub-Api-Version": "2022-11-28"}
    if cfg.token:
        headers["Authorization"] = "Bearer " + cfg.token
    opener = urllib.request.build_opener(_Redirect)
    with opener.open(urllib.request.Request(cfg.api + path, headers=headers), timeout=timeout) as response:
        data = response.read(MAX_ARCHIVE + 1)
    if len(data) > MAX_ARCHIVE:
        raise ValueError("archive trop volumineuse")
    return data


def remote_commit(cfg: Config) -> str:
    sha = fetch(cfg, f"/repos/{cfg.repo}/commits/{urllib.parse.quote(cfg.ref, safe='')}",
                "application/vnd.github.sha", timeout=20).decode("ascii", "replace").strip()
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("réponse de GitHub inattendue pour le commit")
    return sha


def download(cfg: Config, sha: str) -> Path:
    """Extrait app/, config/, docker/, requirements.txt et Dockerfile du commit dans releases/<sha>."""
    target = cfg.releases / sha
    if (target / "app" / "main.py").is_file():
        return target
    raw = fetch(cfg, f"/repos/{cfg.repo}/tarball/{sha}", "application/vnd.github+json", timeout=180)
    staging = Path(tempfile.mkdtemp(prefix=".telechargement-", dir=cfg.releases))
    try:
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as archive:
            members = []
            for member in archive.getmembers():
                parts = member.name.split("/", 1)       # GitHub préfixe tout par « <dépôt>-<commit>/ »
                if len(parts) != 2 or not parts[1]:
                    continue
                if parts[1].startswith(KEPT_DIRS) or parts[1] in KEPT_FILES:
                    member.name = parts[1]
                    members.append(member)
            # Filtre « data » : refuse chemins absolus, « .. », liens sortants et fichiers spéciaux.
            archive.extractall(staging, members=members, filter="data")
        if not (staging / "app" / "main.py").is_file():
            raise ValueError("l'archive ne contient pas app/main.py")
        if target.exists():
            shutil.rmtree(target)
        os.replace(staging, target)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return target


def requirements_key(path: Path) -> str:
    lines = (line.split("#", 1)[0].strip() for line in path.read_text(encoding="utf-8").splitlines())
    return hashlib.sha256("\n".join(sorted(line for line in lines if line)).encode()).hexdigest()[:16]


def python_for(cfg: Config, code: Path) -> str:
    """Interpréteur à utiliser : celui de l'image, ou un environnement avec les nouvelles dépendances."""
    wanted = code / "requirements.txt"
    if not wanted.is_file() or requirements_key(wanted) == requirements_key(cfg.baked / "requirements.txt"):
        return sys.executable
    venv = cfg.venvs / requirements_key(wanted)
    python = venv / "bin" / "python"
    if (venv / ".pret").is_file() and python.exists():
        return str(python)
    log("Les dépendances ont changé : installation (une seule fois, quelques minutes au plus)…")
    shutil.rmtree(venv, ignore_errors=True)
    cfg.venvs.mkdir(parents=True, exist_ok=True)
    # --system-site-packages : seules les dépendances nouvelles ou changées sont téléchargées.
    subprocess.run([sys.executable, "-m", "venv", "--system-site-packages", str(venv)], check=True, timeout=300)
    subprocess.run([str(python), "-m", "pip", "install", "--no-cache-dir", "--disable-pip-version-check", "-q",
                    "-r", str(wanted)], check=True, timeout=1500)
    (venv / ".pret").write_text(time.strftime("%Y-%m-%dT%H:%M:%S"), encoding="utf-8")
    return str(python)


def loads(python: str, code: Path) -> tuple[bool, str]:
    """Le code se charge-t-il ? (import de app.main, sans connexion à la base)"""
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    try:
        result = subprocess.run([python, "-c", "import app.main"], cwd=code, env=env, capture_output=True,
                                text=True, timeout=180)
    except subprocess.TimeoutExpired:
        return False, "délai dépassé au chargement du code"
    return result.returncode == 0, (result.stderr or result.stdout).strip()[-3000:]


def version_of(code: Path) -> str:
    try:
        match = re.search(r'VERSION\s*=\s*"([^"]+)"', (code / "app" / "__init__.py").read_text(encoding="utf-8"))
        return match[1] if match else "?"
    except OSError:
        return "?"


def read_state(cfg: Config) -> dict | None:
    try:
        state = json.loads(cfg.state_file.read_text(encoding="utf-8"))
        return state if isinstance(state, dict) else None
    except (OSError, ValueError):
        return None


def usable(state: dict | None) -> bool:
    return bool(state) and (Path(state.get("code_dir", "")) / "app" / "main.py").is_file() \
        and Path(state.get("python", "")).exists()


def baked_state(cfg: Config) -> dict:
    return {"source": "image", "commit": None, "ref": None, "repo": cfg.repo or None, "code_dir": str(cfg.baked),
            "python": sys.executable, "version": version_of(cfg.baked)}


def cleanup(cfg: Config, keep: list[dict]) -> None:
    """Garde la version lancée et la précédente (retour arrière possible), supprime le reste."""
    keep_dirs = {Path(s["code_dir"]).resolve() for s in keep if s}
    keep_venvs = {Path(s["python"]).resolve().parent.parent for s in keep if s}
    for folder in (cfg.releases, cfg.venvs):
        if not folder.is_dir():
            continue
        for item in folder.iterdir():
            if item.resolve() not in keep_dirs and item.resolve() not in keep_venvs:
                shutil.rmtree(item, ignore_errors=True)


def warn_if_image_outdated(code: Path) -> None:
    for name, released in (("Dockerfile", code / "Dockerfile"), ("entrypoint.py", code / "docker" / "entrypoint.py")):
        current = IMAGE_DIR / name
        if released.is_file() and current.is_file() and released.read_bytes() != current.read_bytes():
            log("Le démarrage du conteneur a changé dans cette version : reconstruis l'image une fois avec "
                "« docker compose up -d --build ». L'application fonctionne en attendant.")
            return


def prepare(cfg: Config) -> dict:
    cfg.releases.mkdir(parents=True, exist_ok=True)
    previous = read_state(cfg)
    chosen = None
    if not cfg.repo:
        log("Mise à jour depuis GitHub désactivée (REPERE_GITHUB_REPO vide).")
    else:
        try:
            sha = remote_commit(cfg)
            if previous and previous.get("commit") == sha and usable(previous):
                chosen = previous
                log(f"Déjà à jour : commit {sha[:7]} (branche {cfg.ref}).")
            else:
                log(f"Nouvelle version validée sur {cfg.repo} : commit {sha[:7]} (branche {cfg.ref}). Téléchargement…")
                code = download(cfg, sha)
                python = python_for(cfg, code)
                ok, error = loads(python, code)
                if ok:
                    chosen = {"source": "github", "commit": sha, "ref": cfg.ref, "repo": cfg.repo, "code_dir": str(code),
                              "python": python, "version": version_of(code),
                              "installed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
                    log(f"Version {chosen['version']} installée.")
                else:
                    log("Le code téléchargé ne se charge pas ; la version précédente est conservée.\n" + error)
        except urllib.error.HTTPError as exc:
            if exc.code in (404, 422):
                log(f"Branche « {cfg.ref} » introuvable sur {cfg.repo}. GitHub Actions la crée quand les tests "
                    "réussissent sur main (dépôt privé : renseigne REPERE_GITHUB_TOKEN).")
            elif exc.code in (401, 403):
                log("GitHub refuse l'accès (jeton REPERE_GITHUB_TOKEN invalide ou expiré, ou trop de requêtes).")
            else:
                log(f"GitHub répond HTTP {exc.code}.")
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            log(f"GitHub injoignable ({getattr(exc, 'reason', exc)}).")
        except subprocess.CalledProcessError:
            log("Installation des nouvelles dépendances impossible (réseau ?) ; la version précédente est conservée.")
        except subprocess.TimeoutExpired:
            log("Installation des nouvelles dépendances trop longue ; la version précédente est conservée.")
        except (tarfile.TarError, ValueError, OSError) as exc:
            log(f"Version téléchargée refusée : {exc}")
    if chosen is None:
        if usable(previous):
            chosen = previous
            log(f"Version conservée : {chosen.get('version')} (commit {(chosen.get('commit') or 'image')[:7]}).")
        else:
            chosen = baked_state(cfg)
            log(f"Code inclus dans l'image utilisé : version {chosen['version']}.")
    chosen = {**chosen, "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    cfg.state_file.write_text(json.dumps(chosen, ensure_ascii=False, indent=1), encoding="utf-8")
    cleanup(cfg, [chosen, previous if usable(previous) else None])
    warn_if_image_outdated(Path(chosen["code_dir"]))
    return chosen


def show_token(cfg: Config, state: dict) -> None:
    if os.getenv("REPERE_TOKEN") or (cfg.data / "connection_token.txt").exists():
        log("Code de connexion : docker compose exec app repere token")
        return
    # Première installation : le code est créé par l'application elle-même, puis affiché une fois ici.
    result = subprocess.run([state["python"], "-m", "app.manage", "token"], cwd=state["code_dir"],
                            capture_output=True, text=True, timeout=60)
    if result.returncode == 0:
        log("Code de connexion (première installation), à coller dans Repère et dans l'extension : " + result.stdout.strip())


def main() -> None:
    cfg = Config.from_env()
    if sys.argv[1:]:
        # Commande explicite (ex. « docker compose run --rm app sh ») : exécutée telle quelle.
        os.execvp(sys.argv[1], sys.argv[1:])
    state = prepare(cfg)
    try:
        show_token(cfg, state)
    except (OSError, subprocess.SubprocessError):
        pass
    os.chdir(state["code_dir"])
    log(f"Démarrage de Repère {state.get('version')} sur http://127.0.0.1:8765")
    os.execv(state["python"], [state["python"], "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0",
                               "--port", "8765", "--app-dir", state["code_dir"]])


if __name__ == "__main__":
    main()
