"""Configuration commune des tests : base SQLite temporaire (même si DATABASE_URL existe), code de connexion
de test, aucun appel réseau (ADEME, data.gouv.fr). Les variables sont fixées avant tout import de l'application."""
import os
import tempfile
from pathlib import Path

import pytest

_DATA = Path(tempfile.mkdtemp(prefix="repere-tests-"))
os.environ["REPERE_DATA_DIR"] = str(_DATA)
os.environ["DATABASE_URL"] = "sqlite:///" + str(_DATA / "tests.sqlite3")
os.environ["REPERE_TOKEN"] = "repere-test-token"
os.environ.setdefault("ADEME_AUTO_SEARCH", "0")
# Pas d'appel à geo.api.gouv.fr ni à data.gouv.fr : les tests du marché simulent ces services.
os.environ.setdefault("MARKET_LOOKUP", "0")

URL = "https://www.jinka.fr/ad/31888795-b893-4ec9-90f2-e577d7d849ae"


@pytest.fixture
def client():
    """Client authentifié sur une base vide à chaque test."""
    from fastapi.testclient import TestClient
    from app import main
    from app.db import Base, engine, initialize
    Base.metadata.drop_all(engine)
    initialize()
    with TestClient(main.app, base_url="http://127.0.0.1:8765", headers={"X-Repere-Token": "repere-test-token"}) as test_client:
        yield test_client
    Base.metadata.drop_all(engine)


@pytest.fixture
def capture():
    """Capture d'extension fictive : prix 234 000 €, 43,3 m², 120 € de charges par mois."""
    return {"url": URL, "title": "Appartement ancien à Cergy | Jinka",
            "text": "Appartement ancien à Cergy\nPrix : 234 000 €\nSurface habitable : 43,3 m²\n"
                    "Charges de copropriété : 120 € par mois\nDPE : D\nBel appartement lumineux proche des commerces.",
            "html": "", "metadata": {}, "json_ld": [], "selection_only": False, "use_gemini": False, "capture_warnings": []}
