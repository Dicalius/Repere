"""Comparaison au marché local, avec deux jeux de données publics de data.gouv.fr (Licence Ouverte) :

- achat : « Statistiques DVF » — prix médian au m² des ventes réelles de la commune sur les cinq dernières
  années (dix semestres), pour les appartements ou les maisons ;
- location : « Carte des loyers » (ANIL et ministère du Logement) — loyer d'annonce au m², charges comprises,
  d'un logement non meublé de référence, avec son intervalle de prédiction.

Envoyés : le code postal et la commune à geo.api.gouv.fr (pour obtenir le code INSEE), puis ce code INSEE à
l'API tabulaire de data.gouv.fr. Ni prix, ni adresse, ni URL d'annonce. Désactivable avec MARKET_LOOKUP=0.
Seule la référence (médiane, indicateur) est conservée dans la fiche : l'écart est recalculé à chaque affichage,
donc une correction du prix ou de la surface est prise en compte sans nouvel appel.
"""
from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import urllib3

from .parsing.rules import fold

GEO_URL = "https://geo.api.gouv.fr/communes"
TABULAR_URL = "https://tabular-api.data.gouv.fr/api/resources/{}/data/"
DVF_RESOURCE = os.getenv("DVF_STATS_RESOURCE", "851d342f-9c96-41c1-924a-11a7a7aae8a6")
# Carte des loyers (indicateurs 2025) : appartements, appartements 1-2 pièces, 3 pièces et plus, maisons.
# Un nouveau jeu paraît chaque année avec de nouveaux identifiants : CARTE_LOYERS_RESOURCES les remplace.
RENT_KEYS = ("appartement", "t1t2", "t3plus", "maison")
RENT_RESOURCES = dict(zip(RENT_KEYS, (os.getenv("CARTE_LOYERS_RESOURCES") or ",".join((
    "55b34088-0964-415f-9df7-d87dd98a09be", "14a1fe11-b2d1-49b3-9f6b-83d12df9482c",
    "5e3b28a4-cf56-43a3-ae79-43cceeb27f8c", "129f764d-b613-44e4-952c-5ff50a8c9b73"))).split(",")))
RENT_YEAR = os.getenv("CARTE_LOYERS_ANNEE", "2025")
RENT_LABELS = {"appartement": "appartements", "t1t2": "appartements de 1 ou 2 pièces",
               "t3plus": "appartements de 3 pièces ou plus", "maison": "maisons"}
SALE_LABELS = {"appartement": "appartements", "maison": "maisons", "apt_maison": "logements"}
PLM = {"75056", "13055", "69123"}          # Paris, Marseille, Lyon : chiffres publiés par arrondissement
MIN_SALES = 10
REFRESH_DAYS = 30
CACHE_SECONDS = 24 * 3600
_http = urllib3.PoolManager(retries=False)
_cache: dict[str, tuple[float, object]] = {}


class MarketError(Exception):
    pass


def enabled() -> bool:
    return os.getenv("MARKET_LOOKUP", "1") != "0"


def norm_city(value) -> str:
    """« Saint-Germain-en-Laye », « St Germain en Laye » → « saint germain en laye » ; « Paris 11e » → « paris »."""
    text = re.sub(r"[-'’]", " ", fold(value or ""))
    text = re.sub(r"\b(ste?)\b", lambda m: "sainte" if m[1] == "ste" else "saint", text)
    text = re.sub(r"\barrondissement\b|\b\d+\s*(?:er|e|eme)\b|\b\d{5}\b", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _get(url: str, params: dict, timeout: float, service: str):
    full = url + "?" + urlencode(params)
    hit = _cache.get(full)
    if hit and time.monotonic() - hit[0] < CACHE_SECONDS:
        return hit[1]
    try:
        response = _http.request("GET", full, headers={"Accept": "application/json", "User-Agent": "Repere (usage personnel)"},
                                 timeout=urllib3.Timeout(connect=min(5, timeout), read=timeout))
    except urllib3.exceptions.HTTPError as exc:
        raise MarketError(f"{service} injoignable") from exc
    if response.status != 200:
        raise MarketError(f"{service} a répondu HTTP {response.status}")
    try:
        data = json.loads(response.data)
    except ValueError as exc:
        raise MarketError(f"réponse inattendue de {service}") from exc
    _cache[full] = (time.monotonic(), data)
    return data


def _number(value):
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def commune(postal_code: str | None, city: str | None, timeout: float = 5) -> dict | None:
    """{"code", "nom"} de la commune (ou de l'arrondissement à Paris, Lyon, Marseille), sinon None."""
    if postal_code:
        found = _get(GEO_URL, {"codePostal": postal_code, "type": "commune-actuelle,arrondissement-municipal",
                               "fields": "nom,code", "format": "json"}, timeout, "geo.api.gouv.fr")
    elif city:
        found = _get(GEO_URL, {"nom": city, "type": "commune-actuelle,arrondissement-municipal", "fields": "nom,code",
                               "boost": "population", "limit": "5", "format": "json"}, timeout, "geo.api.gouv.fr")
    else:
        return None
    found = [c for c in found if isinstance(c, dict) and re.fullmatch(r"[0-9AB]{5}", str(c.get("code", "")))] \
        if isinstance(found, list) else []
    detailed = [c for c in found if c["code"] not in PLM]
    if postal_code and detailed and len(detailed) < len(found):
        found = detailed                     # 75011 → Paris 11e Arrondissement plutôt que Paris
    if len(found) == 1:
        return {"code": found[0]["code"], "nom": found[0].get("nom")}
    target = norm_city(city)
    exact = [c for c in found if target and norm_city(c.get("nom")) == target]
    if not exact and not postal_code and found:
        exact = found[:1]                    # recherche par nom : la commune la plus peuplée
    return {"code": exact[0]["code"], "nom": exact[0].get("nom")} if exact else None


def _rows(data, column: str, code: str) -> list:
    rows = data.get("data") if isinstance(data, dict) else None
    return [r for r in rows or [] if isinstance(r, dict) and str(r.get(column)) == code]


def sale_reference(code: str, kind: str, timeout: float = 5) -> dict:
    rows = _rows(_get(TABULAR_URL.format(DVF_RESOURCE), {"code_geo__exact": code, "page_size": "5"}, timeout,
                      "data.gouv.fr (statistiques DVF)"), "code_geo", code)
    row = next((r for r in rows if r.get("echelle_geo") in (None, "commune")), None)
    if not row:
        return {"missing": True}
    wanted = kind if kind in ("appartement", "maison") else "apt_maison"
    for suffix in dict.fromkeys((wanted, "apt_maison")):
        median, sales = _number(row.get(f"med_prix_m2_whole_{suffix}")), _number(row.get(f"nb_ventes_whole_{suffix}"))
        if median and sales and sales >= MIN_SALES:
            return {"median_m2": median, "mean_m2": _number(row.get(f"moy_prix_m2_whole_{suffix}")), "sales": int(sales),
                    "kind": SALE_LABELS[suffix], "source": "Statistiques DVF (data.gouv.fr), ventes des 5 dernières années"}
    return {"missing": True, "sales": int(_number(row.get(f"nb_ventes_whole_{wanted}")) or 0)}


def rooms_count(rooms) -> int | None:
    match = re.search(r"\d+", str(rooms or ""))
    return int(match[0]) if match else (1 if "studio" in fold(rooms or "") else None)


def rent_key(kind: str, rooms) -> str:
    if kind == "maison":
        return "maison"
    count = rooms_count(rooms)
    return "appartement" if count is None else "t1t2" if count <= 2 else "t3plus"


def rent_reference(code: str, kind: str, rooms, timeout: float = 5) -> dict:
    key = rent_key(kind, rooms)
    rows = _rows(_get(TABULAR_URL.format(RENT_RESOURCES[key]), {"INSEE_C__exact": code, "page_size": "5"}, timeout,
                      "data.gouv.fr (carte des loyers)"), "INSEE_C", code)
    m2 = _number(rows[0].get("loypredm2")) if rows else None
    if not m2:
        return {"missing": True}
    row = rows[0]
    return {"m2": round(m2, 2), "low": _number(row.get("lwr.IPm2")), "high": _number(row.get("upr.IPm2")),
            "level": str(row.get("TYPPRED") or "").lower(), "observations": int(_number(row.get("nbobs_com")) or 0),
            "r2": _number(row.get("R2_adj")), "kind": RENT_LABELS[key],
            "source": f"Carte des loyers {RENT_YEAR} (ANIL, ministère du Logement), loyer d'annonce charges comprises, non meublé"}


def key(facts: dict, kind: str) -> str:
    """Ce dont dépend la référence : si l'un de ces éléments change dans la fiche, elle est recherchée de nouveau."""
    transaction = "Location" if facts.get("transaction") == "Location" else "Achat"
    parts = [transaction, facts.get("postal_code") or "", norm_city(facts.get("city")), kind or ""]
    if transaction == "Location":
        parts.append(rent_key(kind, facts.get("rooms")))
    return "|".join(parts)


def lookup(facts: dict, kind: str) -> dict | None:
    """Référence de marché à conserver dans la fiche ; None si ni code postal ni ville."""
    if not facts.get("postal_code") and not facts.get("city"):
        return None
    reference = {"key": key(facts, kind), "fetched_at": datetime.now(timezone.utc).isoformat()}
    try:
        place = commune(facts.get("postal_code"), facts.get("city"))
        if not place:
            reference["error"] = "Commune introuvable à partir du code postal et de la ville de la fiche."
            return reference
        reference["commune"] = place
        if facts.get("transaction") == "Location":
            reference["rent"] = rent_reference(place["code"], kind, facts.get("rooms"))
        else:
            reference["sale"] = sale_reference(place["code"], kind)
    except MarketError as exc:
        reference["error"] = f"Comparaison au marché indisponible : {exc}."
    return reference


def needs_refresh(reference: dict | None, facts: dict, kind: str) -> bool:
    if not reference:
        return bool(facts.get("postal_code") or facts.get("city"))
    if reference.get("key") != key(facts, kind) or reference.get("error"):
        return True
    try:
        fetched = datetime.fromisoformat(reference["fetched_at"])
    except (KeyError, TypeError, ValueError):
        return True
    return datetime.now(timezone.utc) - fetched > timedelta(days=REFRESH_DAYS)


def compare(reference: dict | None, facts: dict, kind: str) -> dict | None:
    """Écart entre l'annonce (valeurs actuelles de la fiche) et la référence conservée."""
    if not reference:
        return None
    out = {"stale": reference.get("key") != key(facts, kind), "fetched_at": reference.get("fetched_at"),
           "error": reference.get("error"), "commune": (reference.get("commune") or {}).get("nom"),
           "sale": None, "rent": None}
    area = facts.get("area_m2")
    sale = reference.get("sale")
    if sale and facts.get("transaction") != "Location":
        out["sale"] = dict(sale)
        price = facts.get("price_eur")
        if sale.get("median_m2") and price and area:
            price_m2 = price / area
            out["sale"].update(price_m2=round(price_m2), diff_pct=round((price_m2 / sale["median_m2"] - 1) * 100, 1))
    rent = reference.get("rent")
    if rent and facts.get("transaction") == "Location":
        out["rent"] = dict(rent)
        monthly = facts.get("rent_monthly")
        if rent.get("m2") and monthly and area:
            extra = facts.get("rent_charges_monthly") if facts.get("rent_charges_included") == "Non" else 0
            rent_m2 = (monthly + (extra or 0)) / area
            out["rent"].update(rent_m2=round(rent_m2, 2), diff_pct=round((rent_m2 / rent["m2"] - 1) * 100, 1),
                               in_range=(rent.get("low") or 0) <= rent_m2 <= (rent.get("high") or float("inf")),
                               charges_unknown=facts.get("rent_charges_included") not in ("Oui", "Non"),
                               furnished=facts.get("furnished") == "Meublé")
    return out
