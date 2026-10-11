import os

# Pas d'appel à l'ADEME pendant les tests, sauf dans ceux qui simulent son API.
os.environ.setdefault("ADEME_AUTO_SEARCH", "0")
