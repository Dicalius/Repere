"""Gemini comme avis indépendant : réponse validée champ par champ, arbitrage des conflits. Sans appel réseau."""
import json

from app.parsing import gemini
from app.parsing.pipeline import pipeline
from app.schemas import Capture


def test_reponse_toleree_champ_par_champ():
    raw = json.dumps({"facts": {"price_eur": 209000, "rooms": "T2", "postal_code": "Sartrouville", "area_m2": 0,
                                "property_type": "Appartement", "inconnu": 1},
                      "evidence": [{"field": "price_eur", "quote": "209 000 €"}, {"field": "rooms"}]})
    answer, invalid = gemini.tolerant_answer(raw)
    assert (answer.facts.price_eur, answer.facts.rooms) == (209000, "T2")
    assert sorted(invalid) == ["area_m2", "postal_code", "property_type"]
    assert [e.field for e in answer.evidence] == ["price_eur"]


def run(text, found, monkeypatch):
    monkeypatch.setattr(gemini, "extract_ai", lambda context: (found, []))
    return pipeline(Capture(url="https://x.example/ad/1", title="Annonce | Site", text=text, use_gemini=True))


def test_gemini_tranche_un_conflit(monkeypatch):
    r = run("Prix 209 000 €\nautre bloc\nPrix 228 400 €", [{"field": "price_eur", "value": 209000.0, "quote": "Prix 209 000 €"}], monkeypatch)
    assert r["facts"]["price_eur"] == 209000 and any("tranché par Gemini" in w for w in r["warnings"])


def test_gemini_contredit_une_regle_simple(monkeypatch):
    r = run("Prix 228 400 €\nDescription", [{"field": "price_eur", "value": 209000.0, "quote": "209 000"}], monkeypatch)
    assert r["facts"]["price_eur"] is None and "price_eur" in r["conflicts"]
    assert any("ne confirme pas" in w for w in r["warnings"])


def test_gemini_complete_les_champs_absents(monkeypatch):
    r = run("Bel appartement lumineux", [{"field": "city", "value": "Sartrouville", "quote": "Sartrouville"}], monkeypatch)
    assert r["facts"]["city"] == "Sartrouville"
