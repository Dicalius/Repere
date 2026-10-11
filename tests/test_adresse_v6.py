"""Recherche d'adresse sans consommation, regroupement par immeuble, classement par distance et indices
de l'annonce, lancement automatique après une capture. API de l'ADEME simulée (noms de champs réels)."""
import json
import os
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

from app import dpe_lookup
from app.schemas import AddressSearch


def dpe(numero, adresse, surface, etage, dpe="E", ges="B", lat=43.1355, lng=5.7422, annee=1972, date="2024-03-01", **extra):
    return {"numero_dpe": numero, "adresse_ban": adresse, "code_postal_ban": "83150", "nom_commune_ban": "Bandol",
            "surface_habitable_logement": surface, "numero_etage_appartement": etage, "etiquette_dpe": dpe, "etiquette_ges": ges,
            "type_batiment": "appartement", "annee_construction": annee, "date_etablissement_dpe": date,
            "_geopoint": f"{lat},{lng}", **extra}


class FakeHTTP:
    def __init__(self, *answers):
        self.answers, self.calls = list(answers), []

    def request(self, method, url, headers=None, timeout=None):
        self.calls.append(parse_qs(urlsplit(url).query))
        status, body = self.answers.pop(0) if self.answers else (200, {"total": 0, "results": []})
        return SimpleNamespace(status=status, data=json.dumps(body).encode())


ROWS = [
    # même immeuble : trois logements de surface voisine (souvent un DPE d'immeuble décliné)
    dpe("A1", "4 Avenue des Pins", 52.0, 2), dpe("A2", "4 Avenue des Pins", 51.5, 1), dpe("A3", "4 avenue des pins", 52.4, 2),
    # autre adresse, plus loin, autre année de construction
    dpe("B1", "18 Rue du Port", 52.0, 2, lat=43.150, lng=5.760, annee=1995),
]


def criteria(**extra):
    base = dict(postal_code="83150", area_m2=52, dpe="E", ges="B", floor="2", kind="appartement", building_year=1972,
                lat=43.13554, lng=5.74226, geo_precise=True, published="2025-09-24")
    return AddressSearch(**{**base, **extra})


def test_recherche_par_lettres_et_surface(monkeypatch):
    fake = FakeHTTP((200, {"total": 4, "results": ROWS}))
    monkeypatch.setattr(dpe_lookup, "_http", fake)
    r = dpe_lookup.search_addresses(criteria())
    qs = fake.calls[0]["qs"][0]
    assert qs == 'code_postal_ban:"83150" AND etiquette_dpe:"E" AND etiquette_ges:"B" AND surface_habitable_logement:[50.44 TO 53.56]'
    assert "_geopoint" in fake.calls[0]["select"][0] and "annee_construction" in fake.calls[0]["select"][0]
    best, other = r["groups"][0], r["groups"][1]
    assert (best["address"], best["count"]) == ("4 Avenue des Pins", 3)
    assert best["distance_km"] < 0.1 and any("construit en 1995" in d for m in other["members"] for d in m["differences"])
    assert r["confidence"] == "forte"


def test_sans_code_postal_recherche_autour_du_point(monkeypatch):
    fake = FakeHTTP((200, {"total": 1, "results": ROWS[:1]}))
    monkeypatch.setattr(dpe_lookup, "_http", fake)
    dpe_lookup.search_addresses(criteria(postal_code=None, city=None))
    assert fake.calls[0]["geo_distance"] == ["5.742260,43.135540,1500m"]


def test_dpe_posterieur_a_l_annonce_penalise():
    late = dpe("C1", "4 Avenue des Pins", 52.0, 2, date="2026-01-10")
    score, differences, _, _ = dpe_lookup.score_row(late, criteria())
    assert score >= 4 and any("postérieur" in d for d in differences)


def test_point_imprecis_sans_penalite_de_distance():
    far = dpe("D1", "1 Rue Loin", 52.0, 2, lat=43.20, lng=5.80)
    assert dpe_lookup.score_row(far, criteria(geo_precise=False))[0] < dpe_lookup.score_row(far, criteria())[0]


def test_delai_respecte(monkeypatch):
    clock = iter([0, 0, 100, 100, 100])
    monkeypatch.setattr(dpe_lookup.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(dpe_lookup, "_http", FakeHTTP())
    r = dpe_lookup.search_addresses(criteria(), deadline=8)
    assert r["groups"] == [] and "interrompue" in r["warnings"][0]


def test_recherche_automatique_apres_capture(monkeypatch):
    os.environ.setdefault("REPERE_TOKEN", "test")
    from pathlib import Path
    from fastapi.testclient import TestClient
    from app import main
    sale = json.loads((Path(__file__).parent / "fixtures" / "leboncoin_vente.json").read_text(encoding="utf-8"))
    sale["ad"]["location"]["source"] = "address"
    fake = FakeHTTP((200, {"total": 4, "results": ROWS}))
    monkeypatch.setattr(dpe_lookup, "_http", fake)
    monkeypatch.setattr(main.photos, "fetch_photo", lambda *a, **k: (_ for _ in ()).throw(main.HTTPException(409, "test")))
    monkeypatch.setenv("ADEME_AUTO_SEARCH", "1")
    headers = {"X-Repere-Token": main.token()}
    with TestClient(main.app, base_url="http://127.0.0.1:8765") as client:
        listing = client.post("/capture", json={"url": "https://www.leboncoin.fr/ad/ventes_immobilieres/3027789970",
                                                "title": "x", "text": "379 000 €", "site_data": sale}, headers=headers).json()["listing"]
        found = listing["report"]["address_search"]
        assert found["automatic"] and found["groups"][0]["address"] == "4 Avenue des Pins"
        assert listing["facts"]["address"] is None                        # jamais rempli sans action
        assert "geo_distance" not in fake.calls[0]                        # code postal connu : filtre par code postal
        stored = client.get(f"/api/listings/{listing['id']}", headers=headers).json()
        assert stored["report"]["address_search"]["groups"][0]["count"] == 3
        # recherche depuis la fiche : les indices de la capture complètent le formulaire
        fake.answers = [(200, {"total": 1, "results": ROWS[3:]})]
        r = client.post("/api/dpe/address", json={"listing_id": listing["id"], "postal_code": "83150", "area_m2": 52, "dpe": "E"},
                        headers=headers).json()
        assert r["groups"][0]["distance_km"] is not None and any("km du point" in d for d in r["groups"][0]["members"][0]["differences"])
        client.delete(f"/api/listings/{listing['id']}", headers=headers)
