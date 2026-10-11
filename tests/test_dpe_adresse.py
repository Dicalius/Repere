"""Recherche d'adresse via la base DPE de l'ADEME. L'API est simulée avec les vrais noms de champs
du jeu « DPE Logements existants (depuis juillet 2021) » ; aucune donnée réelle."""
import json
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import HTTPException

from app import dpe_lookup
from app.schemas import AddressSearch, Capture
from app.parsing.pipeline import pipeline

ROWS = [
    {"numero_dpe": "2378E0000000A", "date_etablissement_dpe": "2026-03-02", "etiquette_dpe": "A", "etiquette_ges": "A",
     "type_batiment": "appartement", "surface_habitable_logement": 43.2, "adresse_ban": "12 Rue des Tilleuls",
     "nom_commune_ban": "Villefictive", "code_postal_ban": "78500", "numero_etage_appartement": 2,
     "complement_adresse_logement": "Bât B", "conso_5_usages_par_m2_ep": 68.4, "emission_ges_5_usages_par_m2": 2.1},
    {"numero_dpe": "2378E0000000B", "date_etablissement_dpe": "2025-11-20", "etiquette_dpe": "A", "etiquette_ges": "A",
     "type_batiment": "maison", "surface_habitable_logement": 120, "adresse_ban": "3 Allée des Chênes",
     "nom_commune_ban": "Villefictive", "code_postal_ban": "78500", "conso_5_usages_par_m2_ep": 67.8,
     "emission_ges_5_usages_par_m2": 2.0},
    # DPE remplacé par le premier : doit disparaître
    {"numero_dpe": "2378E0000000C", "numero_dpe_remplace": None, "date_etablissement_dpe": "2024-01-10",
     "etiquette_dpe": "A", "etiquette_ges": "A", "type_batiment": "appartement", "surface_habitable_logement": 43.2,
     "adresse_ban": "12 Rue des Tilleuls", "nom_commune_ban": "Villefictive", "code_postal_ban": "78500",
     "conso_5_usages_par_m2_ep": 68.0, "emission_ges_5_usages_par_m2": 2.0},
]
ROWS[0]["numero_dpe_remplace"] = "2378E0000000C"


class FakeHTTP:
    def __init__(self, answers):
        self.answers, self.calls = list(answers), []

    def request(self, method, url, headers=None, timeout=None):
        self.calls.append(url)
        status, body = self.answers.pop(0) if self.answers else (200, {"total": 0, "results": []})
        return SimpleNamespace(status=status, data=json.dumps(body).encode())


def use(monkeypatch, *answers):
    fake = FakeHTTP(answers)
    monkeypatch.setattr(dpe_lookup, "_http", fake)
    return fake


def qs_of(url):
    return parse_qs(urlsplit(url).query)["qs"][0]


def test_requete_stricte_puis_classement(monkeypatch):
    fake = use(monkeypatch, (200, {"total": 3, "results": ROWS}))
    result = dpe_lookup.search_addresses(AddressSearch(postal_code="78500", dpe_kwh_m2=68, ges_kg_m2=2, area_m2=43,
                                                       dpe="A", ges="A", floor="2e étage", kind="appartement"))
    assert qs_of(fake.calls[0]) == ('code_postal_ban:"78500" AND conso_5_usages_par_m2_ep:[67.50 TO 68.50] '
                                    'AND emission_ges_5_usages_par_m2:[1.50 TO 2.50]')
    assert "data.ademe.fr/data-fair/api/v1/datasets/meg-83tjwtg8dyz4vv7h1dqe/lines" in fake.calls[0]
    first = result["candidates"][0]
    assert (first["address"], first["complement"], first["postal_code"]) == ("12 Rue des Tilleuls", "Bât B", "78500")
    assert [c["numero_dpe"] for c in result["candidates"]] == ["2378E0000000A", "2378E0000000B"]  # C remplacé
    assert any("maison" in d for d in result["candidates"][1]["differences"])
    assert first["map_url"].startswith("https://www.openstreetmap.org/search?query=")


def test_elargissement_progressif(monkeypatch):
    fake = use(monkeypatch, (200, {"results": []}), (200, {"results": []}), (200, {"results": ROWS[:1]}))
    result = dpe_lookup.search_addresses(AddressSearch(postal_code="78500", dpe_kwh_m2=68, ges_kg_m2=2))
    assert "emission_ges" not in qs_of(fake.calls[1])
    assert "[66.50 TO 69.50]" in qs_of(fake.calls[2])
    assert result["total"] == 1 and any("élargie" in w for w in result["warnings"])


def test_aucun_resultat(monkeypatch):
    use(monkeypatch)
    result = dpe_lookup.search_addresses(AddressSearch(city='Ville "test"', dpe_kwh_m2=187.3))
    assert result["candidates"] == [] and "pas encore faite" in result["warnings"][0]


def test_erreurs_lisibles(monkeypatch):
    use(monkeypatch, (404, {"error": "not found"}))
    with pytest.raises(HTTPException) as err:
        dpe_lookup.search_addresses(AddressSearch(postal_code="78500", dpe_kwh_m2=68))
    assert "ADEME_DPE_DATASET" in err.value.detail


def test_criteres_minimaux():
    with pytest.raises(ValueError):
        AddressSearch(dpe_kwh_m2=68)                       # pas de lieu
    with pytest.raises(ValueError):
        AddressSearch(postal_code="78500", area_m2=43)     # ni consommation, ni lettre
    AddressSearch(postal_code="78500", area_m2=43, dpe="A")
    AddressSearch(lat=48.9, lng=2.2, area_m2=43, ges="B")


def test_extraction_des_valeurs_du_dpe():
    text = ("Villefictive (78500)\n209 000 €\nConsommation énergétique\n68 kWh/m²/an\nÉmissions de GES\n"
            "2 kg CO₂/m²/an\nDPE : A\nGES : A\n≤ 70\n71 à 110\n111 à 180 kWh/m²/an")
    f = pipeline(Capture(url="https://x.example/ad/1", title="Appartement | Site", text=text))["facts"]
    assert (f["postal_code"], f["dpe_kwh_m2"], f["ges_kg_m2"], f["dpe"], f["ges"]) == ("78500", 68, 2, "A", "A")
