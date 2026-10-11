"""Extraction hiérarchisée : une valeur fiable n'est plus bloquée par le bruit de la page
(annonces similaires, mensualité, prix au m², adresse d'agence…). Annonces fictives."""
import pytest
from app.schemas import Capture
from app.parsing.pipeline import pipeline, clean_html

PAGE = """Appartement 3 pièces 65 m²
Nantes (44000)
250 000 €
3 846 €/m²
Estimation de mensualité
1 150 €/mois
Honoraires : 10 000 € à la charge de l'acquéreur. Prix hors honoraires : 240 000 €
Description
Bel appartement de 3 pièces, séjour de 25 m², 2 chambres, balcon de 6 m². Prix 250 000 € honoraires inclus.
Caractéristiques
Surface habitable
65 m²
Pièces
3
DPE
C
Charges de copropriété : 1 800 € par an
Annonces similaires
Appartement 2 pièces
45 m² · Nantes (44100)
189 000 €
Prix 189 000 €
Appartement 4 pièces
85 m² · Rezé (44400)
310 000 €"""


def facts(title, text, selection=False, html=""):
    capture = Capture(url="https://annonces.example/ad/1", title=title, text=text, selection_only=selection,
                      html=clean_html(html) if html else "")
    return pipeline(capture)["facts"]


@pytest.mark.parametrize("selection", [False, True])
def test_page_avec_annonces_similaires(selection):
    f = facts("Sélection manuelle" if selection else "Appartement 3 pièces 65 m² - Nantes | Site", PAGE, selection)
    assert (f["price_eur"], f["area_m2"], f["city"], f["rooms"], f["dpe"], f["charges_annual"]) == \
        (250000, 65, "Nantes", "T3", "C", 1800)


@pytest.mark.parametrize("title,text,selection,expected", [
    ("Vente maison 5 pièces 120 m² terrain 800 m² à Rezé (44400) - 420 000 €", "Belle maison.", False,
     dict(price_eur=420000, area_m2=120, city="Rezé", rooms="T5")),
    ("Appartement à louer 3 pièces 65 m² Nantes (44000)", "Loyer 950 €/mois\nDépôt de garantie 1 900 €", False, dict(price_eur=None)),
    ("Programme neuf Nantes", "Du T2 au T4\nà partir de 189 000 €", False, dict(price_eur=None)),
    ("Appartement 3 pièces 65 m² | Site", "250 000 €\nPrix au m² 3 846 €\nsoit 3 846 € le m²\nFrais de notaire estimés : 19 000 €",
     False, dict(price_eur=250000)),
    ("Appartement 2 pièces 45 m² | Site", "Nantes (44000)\n189 000 €\nAgence Durand\n12 rue de Paris\n49000 Angers", False,
     dict(city="Nantes")),
    ("Appartement 2 pièces 45 m² | Site", "Photos (12)\nFavoris (10)\n189 000 €", False, dict(city=None, price_eur=189000)),
    ("Sélection manuelle", "189 000 €\n2 pièces · 45 m²\nRezé (44400)", True,
     dict(price_eur=189000, rooms="T2", area_m2=45, city="Rezé")),
    ("Sélection manuelle", "Appartement de 65 m²\nséjour de 25 m², 2 chambres de 12 m², balcon de 6 m²\n3 pièces", True,
     dict(area_m2=65, rooms="T3")),
    # Deux prix sans titre de section ni libellé : le champ reste vide plutôt que faux.
    ("Appartement | Site", "Appartement\n250 000 €\nAppartement\n189 000 €", False, dict(price_eur=None)),
    ("Appartement 3 pièces 65 m² | Site", "DPE\nA\nB\nC\nD\nE\nF\nG", False, dict(dpe="NC")),
    ("Appartement 3 pièces | Site", "Prix : 250 000 € honoraires inclus\nPrix hors honoraires : 240 000 €", False,
     dict(price_eur=250000)),
])
def test_cas_limites(title, text, selection, expected):
    f = facts(title, text, selection)
    assert {k: f[k] for k in expected} == expected


def test_form_englobant_et_header_d_article_conserves():
    html = ('<form id="aspnetForm"><main><article><header><h1>Appartement 3 pièces 65 m²</h1>'
            '<p>Nantes (44000)</p><p>250 000 €</p></header></article></main><input name="__VIEWSTATE"></form>')
    f = facts("", "", html=html)
    assert (f["price_eur"], f["city"]) == (250000, "Nantes")


def test_schema_gemini_accepte_par_le_sdk():
    from google import genai
    from google.genai import _transformers
    from app.parsing.gemini import Answer
    _transformers.t_schema(genai.Client(api_key="test")._api_client, Answer)


def test_capture_avec_annonces_voisines_ecartees():
    """Capture produite par l'extension (page fictive) : les cartes d'annonces voisines ont été retirées par capture.js,
    le détail du prix (net vendeur, honoraires, notaire, coût total) ne doit pas créer de conflit."""
    import json
    from pathlib import Path
    raw = json.loads((Path(__file__).parent / "fixtures" / "capture_annonces_voisines.json").read_text(encoding="utf-8"))
    capture = Capture.model_validate(raw)
    capture.html = clean_html(capture.html)
    f = pipeline(capture)["facts"]
    assert (f["price_eur"], f["area_m2"], f["rooms"], f["city"], f["postal_code"], f["dpe"], f["dpe_kwh_m2"]) == \
        (209000, 43, "T2", "Sartrouville", "78500", "A", 68)
