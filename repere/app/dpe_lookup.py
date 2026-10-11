"""Adresses probables d'un bien, retrouvées dans la base publique des DPE de l'ADEME.

Jeu « DPE Logements existants (depuis juillet 2021) », Licence Ouverte. Une annonce publiée aujourd'hui doit
avoir un DPE valide : ceux d'avant juillet 2021 ont tous expiré (fin 2022 ou fin 2024), donc ce jeu suffit.

Envoyé à l'ADEME : le lieu (code postal, commune ou point de l'annonce), les valeurs ou lettres du DPE et la
surface. Jamais l'URL de l'annonce ni le profil. Les résultats sont regroupés par adresse : dans un immeuble,
plusieurs logements ont souvent des DPE presque identiques, et l'adresse est trouvée même si l'appartement
ne l'est pas. Rien n'est écrit dans la fiche sans action de l'utilisateur.
"""
from __future__ import annotations

import json
import math
import os
import re
import time
from urllib.parse import quote, urlencode

import urllib3
from fastapi import HTTPException

from .schemas import AddressSearch

BASE = "https://data.ademe.fr/data-fair/api/v1/datasets/"
# L'identifiant du jeu a déjà changé plusieurs fois : il reste configurable sans reconstruire l'image.
DATASET = os.getenv("ADEME_DPE_DATASET", "meg-83tjwtg8dyz4vv7h1dqe")
FIELDS = ["numero_dpe", "numero_dpe_remplace", "numero_dpe_immeuble_associe", "date_etablissement_dpe",
          "etiquette_dpe", "etiquette_ges", "type_batiment", "surface_habitable_logement", "adresse_ban", "adresse_brut",
          "nom_commune_ban", "code_postal_ban", "numero_etage_appartement", "complement_adresse_batiment",
          "complement_adresse_logement", "conso_5_usages_par_m2_ep", "emission_ges_5_usages_par_m2",
          "annee_construction", "periode_construction", "nombre_niveau_immeuble", "_geopoint"]
PAGE = 100
MAX_GROUPS = 5
_http = urllib3.PoolManager(retries=False)


def _phrase(value: str) -> str:
    return '"' + re.sub(r'["\\]', " ", value.strip()) + '"'


def _range(field: str, low: float, high: float) -> str:
    return f"{field}:[{low:.2f} TO {high:.2f}]"


def _tolerance(value: float, wide: bool) -> float:
    # Une annonce affiche la valeur arrondie : ±0,5 pour un entier, ±0,05 pour une décimale.
    if wide:
        return 1.5
    return 0.5 if float(value).is_integer() else 0.05


def _places(c: AddressSearch):
    """Filtres de lieu, du plus fiable au plus large. Le point de l'annonce ne sert de filtre que sans code postal."""
    places = []
    if c.postal_code:
        places.append(("code postal " + c.postal_code, {"qs": "code_postal_ban:" + _phrase(c.postal_code)}))
    elif c.lat is not None and c.lng is not None:
        radius = "1500m" if c.geo_precise else "4000m"
        places.append((f"{radius} autour du point de l'annonce", {"geo_distance": f"{c.lng:.6f},{c.lat:.6f},{radius}"}))
    if c.city and c.city.strip():
        places.append(("commune " + c.city.strip(), {"qs": "nom_commune_ban:" + _phrase(c.city)}))
    return places


def attempts(c: AddressSearch):
    """Requêtes de la plus stricte à la plus large ; on s'arrête à la première qui trouve un DPE.
    Avec la consommation en kWh : valeurs du DPE. Sans : lettres DPE/GES et surface habitable."""
    for place_label, place in _places(c):
        plans = []
        if c.dpe_kwh_m2 is not None:
            for wide, with_ges in [(False, True)] + ([(False, False)] if c.ges_kg_m2 is not None else []) + [(True, False)]:
                tol = _tolerance(c.dpe_kwh_m2, wide)
                clauses = [_range("conso_5_usages_par_m2_ep", c.dpe_kwh_m2 - tol, c.dpe_kwh_m2 + tol)]
                label = f"consommation {c.dpe_kwh_m2:g} ±{tol:g}"
                if with_ges and c.ges_kg_m2 is not None:
                    g = _tolerance(c.ges_kg_m2, False)
                    clauses.append(_range("emission_ges_5_usages_par_m2", c.ges_kg_m2 - g, c.ges_kg_m2 + g))
                    label += f", émissions {c.ges_kg_m2:g} ±{g:g}"
                plans.append((label, clauses, wide))
        elif c.area_m2:
            letters = [f"etiquette_dpe:{_phrase(c.dpe)}"] if c.dpe != "NC" else []
            ges = [f"etiquette_ges:{_phrase(c.ges)}"] if c.ges != "NC" else []
            for share, with_ges, wide in ((0.03, True, False), (0.03, False, False), (0.08, False, True)):
                tol = max(1.5, c.area_m2 * share)
                clauses = letters + (ges if with_ges else []) + [_range("surface_habitable_logement", c.area_m2 - tol, c.area_m2 + tol)]
                if not (letters or (ges and with_ges)):
                    continue
                label = " ".join(filter(None, [f"DPE {c.dpe}" if letters else "", f"GES {c.ges}" if with_ges and ges else ""])) \
                    + f", surface {c.area_m2:g} m² ±{tol:.1f}"
                plans.append((label, clauses, wide))
        seen = set()
        for label, clauses, wide in plans:
            key = tuple(clauses)
            if key in seen:
                continue
            seen.add(key)
            params = dict(place)
            params["qs"] = " AND ".join(([params["qs"]] if "qs" in params else []) + clauses)
            yield f"{place_label}, {label}", params, wide


def query(params: dict, timeout: float = 25) -> dict:
    url = BASE + quote(DATASET, safe="") + "/lines?" + urlencode(
        {**params, "size": PAGE, "select": ",".join(FIELDS), "sort": "-date_etablissement_dpe"})
    try:
        response = _http.request("GET", url, headers={"Accept": "application/json", "User-Agent": "Repere/1.3 (usage personnel)"},
                                 timeout=urllib3.Timeout(connect=min(6, timeout), read=timeout))
    except urllib3.exceptions.HTTPError as exc:
        raise HTTPException(502, "Base DPE de l'ADEME injoignable. Vérifie la connexion puis réessaie.") from exc
    if response.status == 404:
        raise HTTPException(502, "Le jeu de données DPE de l'ADEME a changé d'identifiant. Renseigne ADEME_DPE_DATASET dans .env.")
    if response.status != 200:
        detail = re.sub(r"\s+", " ", response.data.decode("utf-8", "replace"))[:200]
        raise HTTPException(502, f"L'ADEME a refusé la recherche (HTTP {response.status}) : {detail}")
    try:
        return json.loads(response.data)
    except ValueError as exc:
        raise HTTPException(502, "Réponse inattendue de l'ADEME.") from exc


def _number(value):
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _point(value):
    try:
        lat, lng = (float(x) for x in str(value).split(","))
        return lat, lng
    except (TypeError, ValueError):
        return None


def distance_km(a, b):
    lat1, lng1, lat2, lng2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lng2 - lng1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


def _normal(text):
    return re.sub(r"\s+", " ", str(text or "").casefold()).strip()


def score_row(row: dict, c: AddressSearch):
    """Écart entre un DPE et l'annonce : plus le score est bas, plus le logement correspond."""
    score, differences = 0.0, []
    kwh, co2 = _number(row.get("conso_5_usages_par_m2_ep")), _number(row.get("emission_ges_5_usages_par_m2"))
    area, floor = _number(row.get("surface_habitable_logement")), row.get("numero_etage_appartement")
    if c.dpe_kwh_m2 is not None and kwh is not None:
        score += abs(kwh - c.dpe_kwh_m2) * 2
    if c.ges_kg_m2 is not None and co2 is not None:
        score += abs(co2 - c.ges_kg_m2) * 2
    if c.area_m2 and area:
        gap = abs(area - c.area_m2) / c.area_m2
        score += gap * 100
        if gap > 0.03:
            differences.append(f"surface {area:g} m² (annonce {c.area_m2:g} m²)")
    for label, wanted, found in (("DPE", c.dpe, row.get("etiquette_dpe")), ("GES", c.ges, row.get("etiquette_ges"))):
        if wanted != "NC" and found and found != wanted:
            score += 10
            differences.append(f"{label} {found} (annonce {wanted})")
    kind = str(row.get("type_batiment") or "").casefold()
    if c.kind and kind in {"appartement", "maison"} and kind != c.kind:
        score += 10
        differences.append(f"{kind} (annonce : {c.kind})")
    digits = re.search(r"\d+", c.floor or "")
    wanted_floor = digits[0] if digits else ("0" if re.match(r"\s*(?:rdc|rez)", c.floor or "", re.I) else None)
    if wanted_floor is not None and floor not in (None, "") and str(floor).strip() != wanted_floor:
        score += 3
        differences.append(f"étage {floor} (annonce {wanted_floor})")
    year = _number(row.get("annee_construction"))
    if c.building_year and year and abs(year - c.building_year) > 2:
        score += 5
        differences.append(f"construit en {year:g} (annonce {c.building_year})")
    levels = _number(row.get("nombre_niveau_immeuble"))
    if c.floors_building and levels and abs(levels - c.floors_building) > 1:
        score += 2
        differences.append(f"immeuble de {levels:g} niveaux (annonce {c.floors_building})")
    dated = str(row.get("date_etablissement_dpe") or "")[:10]
    if c.published and dated and dated > c.published:
        score += 4
        differences.append(f"DPE du {dated}, postérieur à l'annonce")
    distance = None
    point = _point(row.get("_geopoint"))
    if c.lat is not None and c.lng is not None and point:
        distance = distance_km((c.lat, c.lng), point)
        if c.geo_precise and distance > 0.5:
            score += min(15, (distance - 0.5) * 6)
            differences.append(f"à {distance:.1f} km du point de l'annonce")
    return score, differences, distance, point


def rank(rows: list[dict], c: AddressSearch) -> list[dict]:
    replaced = {row.get("numero_dpe_remplace") for row in rows if row.get("numero_dpe_remplace")}
    seen, candidates = set(), []
    for row in rows:
        if row.get("numero_dpe") in replaced:
            continue  # DPE remplacé par un DPE plus récent du même logement
        address = (row.get("adresse_ban") or row.get("adresse_brut") or "").strip()
        if not address:
            continue
        complement = " · ".join(v.strip() for v in (row.get("complement_adresse_batiment"), row.get("complement_adresse_logement"))
                                if isinstance(v, str) and v.strip())
        area = _number(row.get("surface_habitable_logement"))
        key = (_normal(address), _normal(complement), area, row.get("numero_etage_appartement"))
        if key in seen:
            continue
        seen.add(key)
        score, differences, distance, point = score_row(row, c)
        place = " ".join(v for v in (row.get("code_postal_ban"), row.get("nom_commune_ban")) if v)
        candidates.append({
            "address": address, "complement": complement, "postal_code": row.get("code_postal_ban"),
            "city": row.get("nom_commune_ban"), "floor": row.get("numero_etage_appartement"), "area_m2": area,
            "kind": str(row.get("type_batiment") or "").casefold() or None, "dpe": row.get("etiquette_dpe"),
            "ges": row.get("etiquette_ges"), "dpe_kwh_m2": _number(row.get("conso_5_usages_par_m2_ep")),
            "ges_kg_m2": _number(row.get("emission_ges_5_usages_par_m2")), "date": row.get("date_etablissement_dpe"),
            "building_year": _number(row.get("annee_construction")), "numero_dpe": row.get("numero_dpe"),
            "building_dpe": row.get("numero_dpe_immeuble_associe"),
            "distance_km": round(distance, 2) if distance is not None else None,
            "score": round(score, 2), "differences": differences,
            "map_url": (f"https://www.openstreetmap.org/?mlat={point[0]:.6f}&mlon={point[1]:.6f}#map=19/{point[0]:.6f}/{point[1]:.6f}"
                        if point else "https://www.openstreetmap.org/search?query=" + quote(f"{address} {place}".strip()))})
    return sorted(candidates, key=lambda item: item["score"])


def group(candidates: list[dict]) -> list[dict]:
    """Un groupe par adresse : nombre de logements correspondants, meilleur écart, logements détaillés."""
    groups = {}
    for item in candidates:
        key = (_normal(item["address"]), item["postal_code"])
        groups.setdefault(key, []).append(item)
    result = []
    for members in groups.values():
        best = min(members, key=lambda m: m["score"])
        result.append({"address": best["address"], "postal_code": best["postal_code"], "city": best["city"],
                       "score": best["score"], "count": len(members), "map_url": best["map_url"],
                       "distance_km": best["distance_km"], "building_year": best["building_year"],
                       "members": sorted(members, key=lambda m: m["score"])[:8]})
    return sorted(result, key=lambda g: (g["score"], -g["count"]))


def confidence(groups: list[dict]) -> str:
    if not groups:
        return "aucune"
    best = groups[0]["score"]
    gap = groups[1]["score"] - best if len(groups) > 1 else 99
    if best <= 6 and gap >= 8:
        return "forte"
    if best <= 15 and gap >= 3:
        return "moyenne"
    return "faible"


def search_addresses(c: AddressSearch, deadline: float | None = None) -> dict:
    """Interroge l'ADEME jusqu'à trouver des DPE. `deadline` (secondes) borne la durée totale, pour la recherche
    automatique lancée après une capture."""
    started, tried = time.monotonic(), []
    for label, params, wide in attempts(c):
        remaining = None if deadline is None else deadline - (time.monotonic() - started)
        if remaining is not None and remaining < 1:
            break
        tried.append(label)
        data = query(params, timeout=25 if remaining is None else min(25, remaining))
        rows = data.get("results", [])
        if not rows:
            continue
        candidates = rank(rows, c)
        groups = group(candidates)
        level = confidence(groups)
        warnings = []
        if wide:
            warnings.append("Correspondance élargie : les valeurs de l'annonce diffèrent un peu de celles du DPE.")
        if (data.get("total") or 0) > PAGE:
            warnings.append(f"{data['total']} DPE correspondent : seuls les {PAGE} plus récents sont classés. Ajoute la consommation en kWh pour affiner.")
        if level == "faible" and len(groups) > 1:
            warnings.append("Plusieurs adresses correspondent presque autant : compare surface, étage, année et photos.")
        return {"criteria": label, "tried": tried, "total": len(candidates), "confidence": level,
                "groups": groups[:MAX_GROUPS], "candidates": candidates[:MAX_GROUPS], "warnings": warnings,
                "searched_at": time.strftime("%Y-%m-%d")}
    reasons = ("Causes possibles : publication du DPE pas encore faite par l'ADEME (plusieurs semaines), valeur ou "
               "surface mal recopiée dans l'annonce, code postal différent.")
    if deadline is not None and len(tried) < sum(1 for _ in attempts(c)):
        reasons = "Recherche interrompue (délai dépassé) : relance-la depuis la fiche."
    return {"criteria": tried[-1] if tried else "", "tried": tried, "total": 0, "confidence": "aucune", "groups": [],
            "candidates": [], "warnings": ["Aucun DPE correspondant. " + reasons], "searched_at": time.strftime("%Y-%m-%d")}
