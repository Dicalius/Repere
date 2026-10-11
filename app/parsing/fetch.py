"""Lecture publique unique, IP résolue épinglée et redirections contrôlées."""
from urllib.parse import urljoin, urlsplit
import urllib3
from fastapi import HTTPException
from ..security import public_url, public_addresses


def fetch_html(raw):
    url = public_url(raw)
    for _ in range(4):
        p = urlsplit(url)
        port = p.port or (443 if p.scheme == "https" else 80)
        ip = public_addresses(p.hostname, port)[0]
        cls = urllib3.HTTPSConnectionPool if p.scheme == "https" else urllib3.HTTPConnectionPool
        kwargs = {"assert_hostname": p.hostname, "server_hostname": p.hostname, "cert_reqs": "CERT_REQUIRED"} if p.scheme == "https" else {}
        pool = cls(ip, port=port, timeout=urllib3.Timeout(connect=8, read=18), retries=False, **kwargs)
        response = None
        try:
            response = pool.request("GET", p.path + ("?" + p.query if p.query else ""),
                                    headers={"Host": p.netloc, "User-Agent": "Repere/1.0 (personal listing import)", "Accept": "text/html"},
                                    redirect=False, preload_content=False, decode_content=True)
            if response.status in (301, 302, 303, 307, 308):
                location = response.headers.get("Location")
                if not location:
                    raise HTTPException(409, "Redirection sans destination. Utilise l'extension.")
                url = public_url(urljoin(url, location))
                continue
            if response.status != 200:
                raise HTTPException(409, f"Le site répond HTTP {response.status}. Ouvre l'annonce et utilise l'extension.")
            if "html" not in response.headers.get("Content-Type", "").lower():
                raise HTTPException(409, "Cette URL ne renvoie pas du HTML. Utilise la capture navigateur.")
            raw = response.read(2_000_001)
            if len(raw) > 2_000_000:
                raise HTTPException(413, "Page trop volumineuse ; utilise l'extension ou une sélection de texte.")
            # BeautifulSoup interprétera l'encodage annoncé dans le HTML.
            from bs4 import UnicodeDammit
            return url, UnicodeDammit(raw).unicode_markup or raw.decode("utf-8", errors="replace")
        except HTTPException:
            raise
        except (urllib3.exceptions.HTTPError, OSError) as exc:
            raise HTTPException(409, "Lecture du site impossible. Utilise l'extension depuis l'annonce ouverte.") from exc
        finally:
            if response:
                response.close()
            pool.close()
    raise HTTPException(409, "Trop de redirections. Utilise l'extension.")
