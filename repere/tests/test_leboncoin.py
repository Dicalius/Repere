"""Adaptateur Leboncoin. La fixture est une vraie annonce de vente issue d'un échantillon public
(scrapfly-scrapers), réduite comme le fait l'extension : sans nom du vendeur ni identifiant de compte.
Les clés propres aux locations (monthly_charges, furnished, charges_included) n'ont pas été vérifiées
sur une vraie annonce : le cas location ci-dessous est synthétique."""
import copy
import json
import os
from pathlib import Path

import pytest

from app.parsing.pipeline import pipeline
from app.parsing.sites import extract, leboncoin
from app.schemas import Capture

SALE = json.loads((Path(__file__).parent / "fixtures" / "leboncoin_vente.json").read_text(encoding="utf-8"))
URL = "https://www.leboncoin.fr/ad/ventes_immobilieres/3027789970"
NOISE = "\n".join(["Annonces similaires", "Appartement 3 pièces", "Prix 228 400 €", "95 m² · T5 · Bordeaux 33000", "1 150 €/mois"])


def run(capture):
    return pipeline(Capture.model_validate(capture))


def test_vente_reelle_toutes_les_valeurs():
    r = run({"url": URL, "title": "Appt … | leboncoin", "text": "379 000 €\n" + NOISE, "site_data": SALE})
    f = r["facts"]
    assert (f["transaction"], f["price_eur"], f["area_m2"], f["rooms"], f["city"], f["postal_code"]) == \
        ("Achat", 379000, 52, "T2", "Bandol", "83150")
    assert (f["dpe"], f["ges"], f["property_type"], f["charges_annual"], f["floor"], f["delivery"]) == \
        ("E", "B", "Ancien", 1170, "2", "1972")
    assert r["conflicts"] == {} and r["site"] == leboncoin.PARSER
    assert r["title"].startswith("Appt type 2 de 52m2 Bandol")


def test_valeurs_du_site_prioritaires_meme_en_selection():
    r = run({"url": URL, "title": "x", "text": "Prix 228 400 €\n3 pièces", "selection_only": True, "site_data": SALE})
    assert (r["facts"]["price_eur"], r["facts"]["rooms"]) == (379000, "T2")


def test_html_colle_avec_next_data():
    ad = {**SALE["ad"], "owner": {"type": "private", "name": "Nom du vendeur"}}
    html = ('<main><h1>Titre</h1></main><script id="__NEXT_DATA__" type="application/json">'
            + json.dumps({"props": {"pageProps": {"ad": ad}}}) + "</script>")
    data = leboncoin.from_html(html, URL)
    assert data["ad"]["list_id"] == 3027789970 and "Nom du vendeur" not in json.dumps(data)
    assert run({"url": URL, "html": html})["facts"]["price_eur"] == 379000


def test_autre_annonce_ignoree():
    r = extract(SALE, "https://www.leboncoin.fr/ad/ventes_immobilieres/1111111111")
    assert not r["used"] and "navigation interne" in r["warnings"][0]


def test_location_synthetique():
    ad = copy.deepcopy(SALE["ad"])
    ad.update(list_id=3333333333, category_id="10", category_name="Locations", price=[950])
    ad["attributes"] = [a for a in ad["attributes"]
                        if a["key"] not in ("annual_charges", "lease_type", "immo_sell_type", "type_real_estate_sale")]
    ad["attributes"] += [{"key": "lease_type", "value": "rent"}, {"key": "furnished", "value": "1", "value_label": "Meublé"},
                         {"key": "monthly_charges", "key_label": "Charges", "value": "60", "value_label": "60 €"},
                         {"key": "charges_included", "value": "1", "value_label": "Oui"}]
    r = run({"url": "https://www.leboncoin.fr/ad/locations/3333333333", "title": "x", "text": "950 €/mois\n" + NOISE,
             "site_data": {"source": "leboncoin", "ad": ad}})
    f = r["facts"]
    assert (f["transaction"], f["rent_monthly"], f["rent_charges_monthly"], f["rent_charges_included"], f["furnished"]) == \
        ("Location", 950, 60, "Oui", "Meublé")
    assert f["price_eur"] is None and f["charges_annual"] is None


@pytest.mark.parametrize("url,expected", [
    ("https://www.leboncoin.fr/ad/ventes_immobilieres/3027789970", "3027789970"),
    ("https://www.leboncoin.fr/ventes_immobilieres/3027789970.htm", "3027789970"),
    ("https://www.leboncoin.fr/recherche?category=9", None),
])
def test_identifiant_dans_l_url(url, expected):
    assert leboncoin.ad_id(url) == expected


def test_capture_et_photos_de_l_annonce(monkeypatch):
    os.environ.setdefault("REPERE_TOKEN", "test")
    from fastapi.testclient import TestClient
    from app import main
    seen = []

    def fake_fetch(urls, referer=""):
        seen.extend(urls)
        raise main.HTTPException(409, "pas de réseau pendant les tests")

    monkeypatch.setattr(main.photos, "fetch_photo", fake_fetch)
    headers = {"X-Repere-Token": main.token()}
    with TestClient(main.app, base_url="http://127.0.0.1:8765") as client:
        row = client.post("/capture", json={"url": URL, "title": "x", "text": "379 000 €", "site_data": SALE},
                          headers=headers).json()["listing"]
        assert row["facts"]["price_eur"] == 379000
        assert seen and seen[0].startswith("https://img.leboncoin.fr/")
        client.delete(f"/api/listings/{row['id']}", headers=headers)
