"""Achat ou location : détection, lecture des loyers, séparation des champs. Annonces fictives."""
import os

import pytest

from app.parsing.pipeline import pipeline
from app.schemas import Capture


@pytest.mark.parametrize("url,title,text,expected", [
    ("https://agence.example/bien/12", "Location appartement 2 pièces 45 m² Nantes (44000) - 850 € CC",
     "Dépôt de garantie : 800 €\nHonoraires locataire : 450 €",
     dict(transaction="Location", rent_monthly=850, area_m2=45, city="Nantes", rooms="T2", price_eur=None)),
    ("https://portail.example/annonces/locations/appartement/paris/123", "Appartement 3 pièces | Site",
     "Loyer hors charges\n1 200 €\nCharges : 80 €\nDépôt de garantie 1 200 €\nBail de 3 ans",
     dict(transaction="Location", rent_monthly=1200, rent_charges_monthly=80, rent_charges_included="Non")),
    ("https://x.example/a/1", "Studio meublé à louer Lyon 7e", "Loyer : 690 € charges comprises dont 40 € de charges",
     dict(transaction="Location", rent_monthly=690, rent_charges_monthly=40, rent_charges_included="Oui", furnished="Meublé")),
    ("https://x.example/a/2", "Appartement T3 à louer", "Location vide\n950 €/mois\nCuisine meublée et équipée",
     dict(transaction="Location", rent_monthly=950, furnished="Non meublé")),
    ("https://x.example/a/3", "Appartement 2 pièces 40 m² | Agence",
     "Prix : 159 000 €\nVendu loué, loyer actuel 650 €/mois\nFrais de notaire réduits",
     dict(transaction="Achat", price_eur=159000, rent_monthly=None)),
    ("https://portail.example/annonce/vente/rueil/42", "Appartement 3 pièces 65 m²", "250 000 €",
     dict(transaction="Achat", price_eur=250000)),
    ("https://x.example/a/5", "Appartement 2 pièces 43 m² | Site", "209 000 €", dict(transaction="Achat", price_eur=209000)),
    ("https://x.example/a/6", "Location appartement vue mer", "85 € / nuit\nMénage 40 €",
     dict(transaction="Location", rent_monthly=None)),
])
def test_detection_et_loyers(url, title, text, expected):
    f = pipeline(Capture(url=url, title=title, text=text))["facts"]
    assert {k: f[k] for k in expected} == expected


def test_sans_indice_classe_en_achat_avec_avertissement():
    r = pipeline(Capture(url="https://x.example/a/9", title="Appartement 2 pièces", text="Bel appartement"))
    assert r["facts"]["transaction"] == "Achat" and any("non précisé" in w for w in r["warnings"])


def test_fiche_basculee_par_l_utilisateur():
    os.environ.setdefault("REPERE_TOKEN", "test")
    from fastapi.testclient import TestClient
    from app import main
    headers = {"X-Repere-Token": main.token()}
    with TestClient(main.app, base_url="http://127.0.0.1:8765") as client:
        row = client.post("/api/listings", json={"title": "Studio", "facts": {"transaction": "Location", "rent_monthly": 720,
                                                                              "furnished": "Meublé"}}, headers=headers).json()
        assert (row["facts"]["transaction"], row["facts"]["rent_monthly"], row["facts"]["furnished"]) == ("Location", 720, "Meublé")
        edited = client.patch(f"/api/listings/{row['id']}", json={"revision": row["revision"], "facts": {"transaction": "Achat"}},
                              headers=headers).json()
        assert edited["facts"]["transaction"] == "Achat" and edited["overrides"]["transaction"] == "Achat"
        assert client.patch(f"/api/listings/{row['id']}", json={"revision": edited["revision"], "facts": {"furnished": "Oui"}},
                            headers=headers).status_code == 422
        client.delete(f"/api/listings/{row['id']}", headers=headers)
