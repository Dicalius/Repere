"""Photo principale d'une annonce, téléchargée côté serveur puis ré-encodée en JPEG.

L'interface n'affiche jamais d'image distante : la photo est servie par Repère (authentifiée), ce qui
évite les requêtes vers les sites d'annonces à chaque affichage et le blocage des liens directs.
Le téléchargement reprend les protections de la lecture d'URL : adresse publique uniquement, IP
résolue épinglée, redirections vérifiées, taille limitée. Le ré-encodage neutralise le contenu du
fichier d'origine (métadonnées, formats exotiques) et borne sa taille.
"""
from __future__ import annotations

import hashlib
import io
import warnings
from urllib.parse import urljoin, urlsplit

import urllib3
from fastapi import HTTPException
from PIL import Image, ImageOps

from .security import public_addresses, public_url

MAX_BYTES = 12_000_000
MAX_PIXELS = 40_000_000
MIN_SIZE = (320, 200)          # en dessous : logo, pictogramme ou vignette inutilisable
THUMBNAIL = (1024, 768)
ACCEPTED = ("image/jpeg", "image/png", "image/webp", "image/gif", "image/avif")
Image.MAX_IMAGE_PIXELS = MAX_PIXELS


def sources(capture) -> list[str]:
    """URLs candidates, de la plus fiable à la moins fiable : images repérées par l'extension dans
    le contenu principal, puis image de partage de la page (og:image), parfois un logo."""
    found = list(getattr(capture, "images", []) or [])
    for key in ("og:image", "twitter:image"):
        value = (capture.metadata or {}).get(key)
        if value:
            found.append(urljoin(capture.url or "", value.strip()))
    unique = []
    for url in found:
        if url.startswith(("http://", "https://")) and url not in unique and len(url) <= 2048:
            unique.append(url)
    return unique[:6]


def page_images(html: str, base_url: str) -> list[str]:
    """Images de partage déclarées par une page HTML (og:image, twitter:image)."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "lxml")
    found = [urljoin(base_url, tag.get("content", "").strip()) for tag in soup.select(
        "meta[property='og:image'],meta[name='og:image'],meta[name='twitter:image'],meta[property='twitter:image']")
        if tag.get("content", "").strip()]
    return [url for url in dict.fromkeys(found) if url.startswith(("http://", "https://"))][:4]


def _get(url: str, referer: str = "") -> tuple[bytes, str]:
    for _ in range(4):
        # public_url vérifie l'adresse ; la requête garde le chemin et les paramètres d'origine
        # (certaines URLs d'images sont signées et ne supportent pas d'être réordonnées).
        checked, p = urlsplit(public_url(url)), urlsplit(url)
        port = p.port or (443 if p.scheme == "https" else 80)
        ip = public_addresses(checked.hostname, port)[0]
        cls = urllib3.HTTPSConnectionPool if p.scheme == "https" else urllib3.HTTPConnectionPool
        kwargs = {"assert_hostname": checked.hostname, "server_hostname": checked.hostname, "cert_reqs": "CERT_REQUIRED"} if p.scheme == "https" else {}
        pool = cls(ip, port=port, timeout=urllib3.Timeout(connect=6, read=15), retries=False, **kwargs)
        response = None
        try:
            headers = {"Host": checked.netloc, "User-Agent": "Repere/1.1 (photo d'annonce, usage personnel)",
                       "Accept": "image/avif,image/webp,image/jpeg,image/png,image/*;q=0.8"}
            if referer.startswith(("http://", "https://")):
                headers["Referer"] = referer
            response = pool.request("GET", (p.path or "/") + ("?" + p.query if p.query else ""), headers=headers,
                                    redirect=False, preload_content=False, decode_content=True)
            if response.status in (301, 302, 303, 307, 308) and response.headers.get("Location"):
                url = urljoin(url, response.headers["Location"])
                continue
            if response.status != 200:
                raise HTTPException(409, f"Le site de l'image répond HTTP {response.status}.")
            kind = response.headers.get("Content-Type", "").split(";")[0].strip().lower()
            if kind not in ACCEPTED:
                raise HTTPException(409, "Cette adresse ne renvoie pas une photo (JPEG, PNG, WebP, GIF ou AVIF).")
            data = response.read(MAX_BYTES + 1)
            if len(data) > MAX_BYTES:
                raise HTTPException(413, "Photo trop volumineuse.")
            return data, kind
        except HTTPException:
            raise
        except (urllib3.exceptions.HTTPError, OSError) as exc:
            raise HTTPException(409, "Téléchargement de la photo impossible.") from exc
        finally:
            if response:
                response.close()
            pool.close()
    raise HTTPException(409, "Trop de redirections pour la photo.")


def thumbnail(data: bytes) -> bytes:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                if image.width < MIN_SIZE[0] or image.height < MIN_SIZE[1]:
                    raise HTTPException(409, "Image trop petite : probablement un logo ou une icône.")
                image.seek(0)
                picture = ImageOps.exif_transpose(image).convert("RGB")
    except HTTPException:
        raise
    except (OSError, ValueError, Image.DecompressionBombWarning, Image.DecompressionBombError) as exc:
        raise HTTPException(409, "Fichier image illisible.") from exc
    picture.thumbnail(THUMBNAIL)
    out = io.BytesIO()
    picture.save(out, "JPEG", quality=82, optimize=True, progressive=True)
    return out.getvalue()


def fetch_photo(urls: list[str], referer: str = "") -> tuple[bytes, str]:
    """Première photo exploitable parmi les URLs ; lève la dernière erreur si aucune ne convient."""
    error = HTTPException(409, "Aucune photo trouvée pour cette annonce.")
    for url in urls:
        try:
            return thumbnail(_get(url, referer)[0]), url
        except HTTPException as exc:
            error = exc
    raise error


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
