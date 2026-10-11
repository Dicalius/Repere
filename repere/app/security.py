from __future__ import annotations
import ipaddress
import os
import secrets
import socket
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from fastapi import HTTPException, Request
from .db import DATA_DIR


def token():
    configured = os.getenv("REPERE_TOKEN", "")
    if configured:
        return configured
    path = DATA_DIR / "connection_token.txt"
    try:
        with path.open("x", encoding="ascii") as handle:
            handle.write(secrets.token_urlsafe(32))
        path.chmod(0o600)
    except FileExistsError:
        pass
    return path.read_text(encoding="ascii").strip()


def authenticate(request: Request):
    provided = request.headers.get("X-Repere-Token", request.headers.get("X-Immobilier-Token", ""))
    if not provided or not secrets.compare_digest(provided, token()):
        raise HTTPException(401, "Code de connexion absent ou incorrect.")


def public_url(raw: str):
    try:
        p = urlsplit(raw.strip())
        if p.scheme not in ("http", "https") or not p.hostname or p.username or p.password or p.port not in (None, 80, 443):
            raise ValueError()
        host = p.hostname.encode("idna").decode("ascii").lower()
        if host == "localhost" or host.endswith((".localhost", ".local")):
            raise ValueError()
        try:
            if not ipaddress.ip_address(host).is_global:
                raise ValueError("private")
        except ValueError as exc:
            if str(exc) == "private":
                raise
        netloc = ("[" + host + "]") if ":" in host else host
        if p.port and p.port != (443 if p.scheme == "https" else 80):
            netloc += ":" + str(p.port)
        query = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
                 if not k.lower().startswith("utm_") and k.lower() not in {"gclid", "fbclid", "msclkid"}
                 and not (host.removeprefix("www.") == "jinka.fr" and p.path.startswith("/ad/") and k.lower() == "alert_id")]
        return urlunsplit((p.scheme, netloc, p.path or "/", urlencode(sorted(query)), ""))
    except (ValueError, UnicodeError) as exc:
        raise HTTPException(400, "Indique une URL publique HTTP ou HTTPS valide.") from exc


def public_addresses(host, port):
    try:
        ips = list(dict.fromkeys(item[4][0] for item in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)))
        if not ips or any(not ipaddress.ip_address(ip).is_global for ip in ips):
            raise ValueError()
        return ips
    except (ValueError, socket.gaierror) as exc:
        raise HTTPException(400, "L'adresse du site n'est pas publique ou n'est pas résolue.") from exc
