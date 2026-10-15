"""Repères d'une annonce : date de publication, historique des prix relevés, DPE et location, même bien dans
une autre annonce, comparaison au marché local (DVF, carte des loyers) avec des services simulés."""
import copy
import json
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import insights, market

SALE = json.loads((Path(__file__).parent / "fixtures" / "leboncoin_vente.json").read_text(encoding="utf-8"))
LBC_URL = "https://www.leboncoin.fr/ad/ventes_immobilieres/3027789970"
OBSERVED = datetime(2026, 10, 15, 9, 0, tzinfo=timezone.utc)


def dates(**payload):
    return insights.extract_dates(payload, OBSERVED)


# --- Date de publication ---------------------------------------------------------------------------------------

def test_leboncoin_premiere_publication_et_modification():
    site = copy.deepcopy(SALE)
    site["ad"]["index_date"] = "2025-11-02 08:15:00"
    found = dates(url=LBC_URL, site_data=site, text="Publiée le 01/01/2026")
    assert (found["published"], found["updated"]) == ("2025-09-24", "2025-11-02")
    assert found["published_source"].startswith("Leboncoin") and not found["approximate"]
    from app.parsing.sites import leboncoin
    assert leboncoin.reduce_ad({**site["ad"], "owner": {"name": "Vendeur"}})["ad"]["index_date"] == "2025-11-02 08:15:00"


def test_date_posted_des_donnees_structurees():
    block = json.dumps({"@type": "RealEstateListing", "datePosted": "2026-09-30T10:00:00+02:00"})
    assert dates(json_ld=[block], text="")["published"] == "2026-09-30"
    html = '<div itemscope><meta itemprop="datePosted" content="2026-08-01"></div>'
    assert dates(html=html)["published"] == "2026-08-01"


@pytest.mark.parametrize("text, expected, approximate", [
    ("Appartement T3\nPubliée le 12/09/2026 à 10:42", "2026-09-12", False),
    ("Annonce mise en ligne le 3 octobre 2026", "2026-10-03", False),
    ("Date de publication : 01.10.26", "2026-10-01", False),
    ("Mise en ligne il y a 3 jours", "2026-10-12", False),
    ("Publiée il y a 2 semaines", "2026-10-01", False),
    ("En ligne depuis plus de 2 mois", "2026-08-16", True),
    ("Publiée hier", "2026-10-14", False),
    ("Publiée aujourd’hui", "2026-10-15", False),
])
def test_dates_dans_le_texte(text, expected, approximate):
    found = dates(text=text)
    assert (found["published"], found["approximate"]) == (expected, approximate)


def test_date_improbable_ignoree_et_premiere_occurrence_retenue():
    assert dates(text="Publiée le 12/09/2031")["published"] is None
    found = dates(text="Publiée le 02/09/2026. Mise à jour le 10/10/2026.\nAnnonces similaires : publiée le 01/10/2026")
    assert (found["published"], found["updated"]) == ("2026-09-02", "2026-10-10")


# --- Historique des prix, date saisie, réanalyse ------------------------------------------------------------------

def test_historique_des_prix_entre_captures(client, capture):
    first = client.post("/capture", json=capture).json()
    assert first["created"] is True and first["listing"]["insights"]["price"]["observations"] == 1
    capture["text"] = capture["text"].replace("234 000", "225 000")
    again = client.post("/capture", json=capture).json()
    assert again["created"] is False and "déjà suivie" in again["message"]
    price = again["listing"]["insights"]["price"]
    assert (price["first"]["amount"], price["current"]["amount"], price["change_eur"], price["change_pct"]) == (234000, 225000, -9000, -3.8)
    assert any("−3,8 %" in line for line in again["summary"])
    # Une réanalyse relit la même capture : ce n'est pas un nouveau relevé.
    ident = again["listing_id"]
    client.post(f"/api/listings/{ident}/reanalyze", json={"parsers": ["text"]})
    row = client.get(f"/api/listings/{ident}").json()
    assert row["insights"]["price"]["observations"] == 2
    # Un relevé erroné peut être retiré.
    point = row["insights"]["price"]["points"][0]["id"]
    row = client.delete(f"/api/listings/{ident}/prices/{point}").json()
    assert row["insights"]["price"]["observations"] == 1 and row["insights"]["price"]["change_eur"] == 0
    assert client.delete(f"/api/listings/{ident}/prices/{point}").status_code == 404


def test_date_de_publication_saisie_puis_rendue_a_la_capture(client, capture):
    capture["text"] += "\nPubliée le 01/10/2026"
    row = client.post("/capture", json=capture).json()["listing"]
    online = row["insights"]["online"]
    assert online["published"] == "2026-10-01" and online["source"] == "Texte de l'annonce" and online["days"] >= 0
    edited = client.patch(f"/api/listings/{row['id']}", json={"published_on": "2026-09-15"}).json()
    assert edited["overrides"]["published_on"] == "2026-09-15"
    assert client.get(f"/api/listings/{row['id']}").json()["insights"]["online"]["manual"] is True
    assert client.patch(f"/api/listings/{row['id']}", json={"published_on": "2099-01-01"}).status_code == 422
    client.patch(f"/api/listings/{row['id']}", json={"unlock_fields": ["published_on"]})
    assert client.get(f"/api/listings/{row['id']}").json()["insights"]["online"]["published"] == "2026-10-01"


def test_rattrapage_des_captures_existantes(client, capture):
    from sqlalchemy import delete
    from app.db import CaptureRecord, Listing, PricePoint, Session, Setting
    ident = client.post("/capture", json=capture).json()["listing_id"]
    client.post(f"/api/listings/{ident}/reanalyze", json={"parsers": ["text"]})
    capture["text"] = capture["text"].replace("234 000", "229 000")
    client.post("/capture", json=capture)
    # Base d'avant la mise à jour : ni relevés, ni dates, ni type de bien dans le rapport.
    with Session.begin() as db:
        db.execute(delete(PricePoint))
        db.execute(delete(Setting).where(Setting.key == insights.BACKFILL))
        row = db.get(Listing, ident)
        row.report = {k: v for k, v in row.report.items() if k not in ("dates", "kind")}
        assert db.query(CaptureRecord).count() == 3
    insights.backfill()
    insights.backfill()                     # une seule fois
    price = client.get(f"/api/listings/{ident}").json()["insights"]["price"]
    assert [p["amount"] for p in price["points"]] == [234000, 229000]
    with Session() as db:
        assert db.get(Listing, ident).report["kind"] == "appartement"


# --- DPE et location, même bien -----------------------------------------------------------------------------------

def test_calendrier_de_location_selon_le_dpe():
    today = date(2026, 10, 15)
    assert insights.dpe_rental({"dpe": "G", "postal_code": "83150"}, today) == \
        {"dpe": "G", "year": 2025, "banned": True, "rent_frozen": True, "overseas": False}
    assert insights.dpe_rental({"dpe": "F"}, today)["banned"] is False
    assert insights.dpe_rental({"dpe": "G", "postal_code": "97400"}, today)["year"] == 2028
    assert insights.dpe_rental({"dpe": "D"}, today) is None


def row(ident, url, **facts):
    base = {"transaction": "Achat", "postal_code": "83150", "city": "Bandol", "area_m2": 52.0, "dpe": "E", "ges": "B", "rooms": "T2"}
    return SimpleNamespace(id=ident, title=ident, url=url, created_at=OBSERVED, status="À étudier", facts={**base, **facts})


def test_meme_bien_dans_une_autre_annonce():
    rows = [row("a", "https://www.leboncoin.fr/ad/1", dpe_kwh_m2=245, price_eur=379000),
            row("b", "https://www.seloger.com/2", area_m2=52.4, dpe_kwh_m2=245.6, price_eur=389000),
            row("c", "https://x.fr/3", dpe_kwh_m2=180),                       # autre DPE : autre logement
            row("d", "https://x.fr/4", area_m2=70, dpe_kwh_m2=245),           # autre surface
            row("e", "https://x.fr/5", transaction="Location", dpe_kwh_m2=245),
            # Sans consommation : surface, pièces, lettres DPE et GES, étage ; ville faute de code postal.
            row("f", "https://x.fr/6", area_m2=61, postal_code=None, city="BANDOL", floor="2"),
            row("g", "https://x.fr/7", area_m2=61, postal_code=None, city="Bandol", floor="2"),
            row("h", "https://x.fr/8", area_m2=61, city="Bandol", floor="3")]
    index = insights.same_property_index(rows)
    assert [m["id"] for m in index["a"]] == ["b"] and index["b"][0]["host"] == "leboncoin.fr"
    assert index["b"][0]["amount"] == 379000 and "consommation" in index["b"][0]["reason"]
    assert [m["id"] for m in index["f"]] == ["g"]
    assert "c" not in index and "d" not in index and "e" not in index and "h" not in index


def test_meme_bien_visible_dans_la_fiche(client, capture):
    capture["text"] += "\nConsommation énergétique : 245 kWh/m²/an\nCode postal 95000"
    one = client.post("/capture", json=capture).json()["listing"]
    other = client.post("/capture", json={**capture, "url": "https://www.example.fr/annonce/99"}).json()["listing"]
    assert [m["id"] for m in other["insights"]["same_property"]] == [one["id"]]


# --- Marché local -------------------------------------------------------------------------------------------------

class FakeHTTP:
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def request(self, method, url, **kwargs):
        self.calls.append(url)
        for prefix, (status, body) in self.routes.items():
            if url.startswith(prefix):
                return SimpleNamespace(status=status, data=json.dumps(body).encode())
        raise AssertionError("URL inattendue : " + url)


DVF = "https://tabular-api.data.gouv.fr/api/resources/" + market.DVF_RESOURCE
RENT_T1T2 = "https://tabular-api.data.gouv.fr/api/resources/" + market.RENT_RESOURCES["t1t2"]
GEO_PARIS = (200, [{"nom": "Paris", "code": "75056"}, {"nom": "Paris 11e Arrondissement", "code": "75111"}])
DVF_PARIS11 = (200, {"data": [{"code_geo": "75111", "libelle_geo": "Paris 11e Arrondissement", "echelle_geo": "commune",
                               "nb_ventes_whole_appartement": 12422, "med_prix_m2_whole_appartement": 10393,
                               "moy_prix_m2_whole_appartement": 10188, "nb_ventes_whole_maison": 3,
                               "med_prix_m2_whole_maison": 16806, "nb_ventes_whole_apt_maison": 12425,
                               "med_prix_m2_whole_apt_maison": 10393}]})
RENT_PARIS11 = (200, {"data": [{"INSEE_C": "75111", "LIBGEO": "Paris 11e Arrondissement", "loypredm2": 31.2,
                                "lwr.IPm2": 24.9, "upr.IPm2": 39.1, "TYPPRED": "commune", "nbobs_com": 5120, "R2_adj": 0.81}]})


@pytest.fixture
def fake_market(monkeypatch):
    market._cache.clear()
    monkeypatch.setenv("MARKET_LOOKUP", "1")
    fake = FakeHTTP({market.GEO_URL: GEO_PARIS, DVF: DVF_PARIS11, RENT_T1T2: RENT_PARIS11})
    monkeypatch.setattr(market, "_http", fake)
    yield fake
    market._cache.clear()


def test_commune_arrondissement_et_ville_ambigue(fake_market):
    assert market.commune("75011", "Paris") == {"code": "75111", "nom": "Paris 11e Arrondissement"}
    fake_market.routes[market.GEO_URL] = (200, [{"nom": "Cergy", "code": "95127"}, {"nom": "Boisemont", "code": "95074"}])
    market._cache.clear()
    assert market.commune("95000", "cergy")["code"] == "95127"
    assert market.commune("95000", "Pontoise") is None


def test_achat_compare_au_prix_median_dvf(client, capture, fake_market):
    capture.update(title="Appartement 2 pièces Paris 11e", text="Appartement T2\nPrix : 520 000 €\nSurface habitable : 45 m²\nParis (75011)")
    row = client.post("/capture", json=capture).json()["listing"]
    sale = row["insights"]["market"]["sale"]
    assert (sale["median_m2"], sale["price_m2"], sale["kind"], sale["diff_pct"]) == (10393, 11556, "appartements", 11.2)
    assert row["insights"]["market"]["commune"] == "Paris 11e Arrondissement" and not row["insights"]["market"]["stale"]
    calls = len(fake_market.calls)
    # Prix corrigé : écart recalculé sans nouvel appel ; code postal corrigé : référence périmée.
    edited = client.patch(f"/api/listings/{row['id']}", json={"facts": {"price_eur": 467000}}).json()
    assert client.get(f"/api/listings/{edited['id']}").json()["insights"]["market"]["sale"]["diff_pct"] == -0.1
    client.patch(f"/api/listings/{row['id']}", json={"facts": {"postal_code": "75012"}})
    assert client.get(f"/api/listings/{row['id']}").json()["insights"]["market"]["stale"] is True
    assert len(fake_market.calls) == calls
    assert "appartements vendus à Paris 11e" in " ".join(client.post("/capture", json=capture).json()["summary"])


def test_location_comparee_a_la_carte_des_loyers(client, capture, fake_market):
    capture.update(title="Location appartement 2 pièces", url="https://www.example.fr/location/1",
                   text="Location appartement T2\nLoyer : 1 300 € par mois charges comprises\nSurface : 40 m²\nParis (75011)")
    row = client.post("/capture", json=capture).json()["listing"]
    rent = row["insights"]["market"]["rent"]
    assert (rent["m2"], rent["rent_m2"], rent["diff_pct"], rent["in_range"], rent["kind"]) == \
        (31.2, 32.5, 4.2, True, "appartements de 1 ou 2 pièces")


def test_marche_indisponible_ou_desactive(client, capture, fake_market, monkeypatch):
    fake_market.routes[DVF] = (503, {"error": "maintenance"})
    capture["text"] += "\nParis (75011)"
    row = client.post("/capture", json=capture).json()["listing"]
    assert "HTTP 503" in row["insights"]["market"]["error"]
    fake_market.routes[DVF] = DVF_PARIS11
    market._cache.clear()
    refreshed = client.post(f"/api/listings/{row['id']}/market").json()
    assert refreshed["insights"]["market"]["error"] is None and refreshed["insights"]["market"]["sale"]["median_m2"] == 10393
    monkeypatch.setenv("MARKET_LOOKUP", "0")
    assert client.post(f"/api/listings/{row['id']}/market").status_code == 409


def test_sauvegarde_et_restauration_des_releves(client, capture):
    ident = client.post("/capture", json=capture).json()["listing_id"]
    capture["text"] = capture["text"].replace("234 000", "219 000")
    client.post("/capture", json=capture)
    client.patch(f"/api/listings/{ident}", json={"published_on": "2026-09-01"})
    backup = client.get("/api/backup").json()
    assert len(backup["price_points"]) == 2
    old_format = {k: v for k, v in backup.items() if k != "price_points"}
    for saved in (backup, old_format):
        client.delete(f"/api/listings/{ident}")
        assert client.post("/api/restore", json=saved).json()["added"] == 1
        row = client.get(f"/api/listings/{ident}").json()
        assert [p["amount"] for p in row["insights"]["price"]["points"]] == [234000, 219000]
        assert row["insights"]["online"]["published"] == "2026-09-01"
