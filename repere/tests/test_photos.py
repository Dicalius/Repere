"""Photo d'annonce : choix de l'image, ré-encodage sûr et routes de l'API. Aucun appel réseau."""
import io
import os
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from PIL import Image

from app import photos


def jpeg(width, height, color=(40, 120, 90)):
    out = io.BytesIO()
    Image.new("RGB", (width, height), color).save(out, "JPEG")
    return out.getvalue()


def test_sources_ordre_et_filtrage():
    capture = SimpleNamespace(url="https://site.example/ad/1", images=["https://cdn.example/a.jpg", "javascript:alert(1)"],
                              metadata={"og:image": "/media/og.jpg", "twitter:image": "https://cdn.example/a.jpg"})
    assert photos.sources(capture) == ["https://cdn.example/a.jpg", "https://site.example/media/og.jpg"]


def test_reencodage_et_taille_bornee():
    data = photos.thumbnail(jpeg(3000, 2000))
    with Image.open(io.BytesIO(data)) as image:
        assert image.format == "JPEG" and image.width <= 1024 and image.height <= 768


@pytest.mark.parametrize("payload", [jpeg(120, 40), b"<svg onload=alert(1)>", b"GIF89a pas une image"])
def test_images_refusees(payload):
    with pytest.raises(HTTPException):
        photos.thumbnail(payload)


def test_bombe_de_decompression_refusee(monkeypatch):
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 1_000_000)
    with pytest.raises(HTTPException):
        photos.thumbnail(jpeg(2000, 1500))


def test_adresse_privee_refusee():
    for url in ("http://127.0.0.1/photo.jpg", "http://169.254.169.254/latest/meta-data", "http://localhost/x.jpg"):
        with pytest.raises(HTTPException):
            photos._get(url)


def test_premiere_photo_exploitable(monkeypatch):
    answers = {"https://cdn.example/logo.png": jpeg(100, 50), "https://cdn.example/salon.jpg": jpeg(1600, 1200)}
    monkeypatch.setattr(photos, "_get", lambda url, referer="": (answers[url], "image/jpeg"))
    data, source = photos.fetch_photo(list(answers), "https://site.example/ad/1")
    assert source == "https://cdn.example/salon.jpg" and data[:2] == b"\xff\xd8"


def test_routes_photo(tmp_path, monkeypatch):
    os.environ.setdefault("REPERE_TOKEN", "test")
    from fastapi.testclient import TestClient
    from app import main
    monkeypatch.setattr(main.photos, "_get", lambda url, referer="": (jpeg(1600, 1200), "image/jpeg"))
    headers = {"X-Repere-Token": main.token()}
    with TestClient(main.app, base_url="http://127.0.0.1:8765") as client:
        capture = {"url": "https://site.example/ad/photo-test", "title": "Appartement 2 pièces 43 m²",
                   "text": "Appartement 2 pièces 43 m²\n209 000 €", "images": ["https://cdn.example/salon.jpg"]}
        listing = client.post("/capture", json=capture, headers=headers).json()["listing"]
        ident = listing["id"]
        rows = client.get("/api/listings", headers=headers).json()
        assert next(r for r in rows if r["id"] == ident)["photo"]
        image = client.get(f"/api/listings/{ident}/photo", headers=headers)
        assert image.status_code == 200 and image.headers["content-type"] == "image/jpeg"
        assert client.get(f"/api/listings/{ident}/photo").status_code == 401
        assert client.post(f"/api/listings/{ident}/photo", json={"url": "https://cdn.example/autre.jpg"}, headers=headers).json()["photo"]
        client.delete(f"/api/listings/{ident}", headers=headers)
        assert client.get(f"/api/listings/{ident}/photo", headers=headers).status_code == 404
