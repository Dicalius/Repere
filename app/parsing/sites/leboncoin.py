"""Adaptateur Leboncoin : lit l'objet annonce que le site publie dans sa page (Next.js).

La page d'une annonce embarque `<script id="__NEXT_DATA__">` ; l'annonce complète se trouve dans
`props.pageProps.ad` : prix (`price` = [montant]), catégorie, `location` (ville, code postal, quartier)
et une liste `attributes` d'objets {key, key_label, value, values, value_label}. Ces valeurs sont saisies
dans les champs du formulaire Leboncoin : elles sont plus fiables que tout ce qu'on peut lire dans le texte.

L'extension réduit l'objet avant l'envoi (voir `reduce_ad`, même liste côté JavaScript) : ni nom du
vendeur, ni identifiant de compte, ni téléphone.
"""
from __future__ import annotations

import json
import re
from urllib.parse import urlsplit

from ..rules import fold, number

SOURCE = "leboncoin"
PARSER = "Leboncoin · données de l'annonce"
# Attributs sans intérêt pour la fiche, ou qui identifient le vendeur.
DROPPED_ATTRIBUTES = {"profile_picture_url", "store_logo", "store_name", "online_store_id", "external_ad_id",
                      "custom_ref", "activity_sector", "has_visibility_option", "is_import", "district_visibility",
                      "district_type_id", "district_resolution_type", "district_id", "new_seller_type"}
# « source » vaut « address » quand le vendeur a saisi une adresse (point géocodé), « city » pour le centre de la commune.
LOCATION_KEYS = ("city", "zipcode", "city_label", "district", "department_name", "region_name", "lat", "lng", "source")


def matches(url: str) -> bool:
    host = (urlsplit(url or "").hostname or "").lower()
    return host == "leboncoin.fr" or host.endswith(".leboncoin.fr")


def ad_id(url: str) -> str | None:
    """Identifiant d'annonce dans l'URL : /ad/ventes_immobilieres/3027789970 ou /ventes_immobilieres/3027789970.htm"""
    match = re.search(r"/(\d{6,12})(?:\.htm)?/?$", urlsplit(url or "").path)
    return match[1] if match else None


def reduce_ad(ad: dict) -> dict:
    """Garde uniquement ce qui décrit le bien. Doit rester alignée sur reduceLeboncoinAd() de capture.js."""
    def text(value, limit=4000):
        return str(value)[:limit] if value is not None else None
    attributes = []
    for item in ad.get("attributes") or []:
        if not isinstance(item, dict) or item.get("key") in DROPPED_ATTRIBUTES:
            continue
        attributes.append({k: (text(item.get(k), 300) if k != "values" else [text(v, 100) for v in (item.get("values") or [])[:20]])
                           for k in ("key", "key_label", "value", "value_label", "values") if item.get(k) is not None})
    images = ad.get("images") or {}
    location = ad.get("location") or {}
    return {
        "source": SOURCE,
        "ad": {
            "list_id": ad.get("list_id"), "subject": text(ad.get("subject"), 300), "body": text(ad.get("body"), 20000),
            "price": [p for p in (ad.get("price") or []) if isinstance(p, (int, float))][:2],
            "price_cents": ad.get("price_cents") if isinstance(ad.get("price_cents"), (int, float)) else None,
            "category_id": text(ad.get("category_id"), 10), "category_name": text(ad.get("category_name"), 100),
            "ad_type": text(ad.get("ad_type"), 20), "url": text(ad.get("url"), 300),
            # Première mise en ligne, puis dernière modification ou remontée en tête de liste.
            "first_publication_date": text(ad.get("first_publication_date"), 40),
            "index_date": text(ad.get("index_date"), 40),
            "owner_type": text((ad.get("owner") or {}).get("type") or ad.get("owner_type"), 20),
            "location": {k: location.get(k) for k in LOCATION_KEYS if location.get(k) not in (None, "")},
            "images": [u for u in ((images.get("urls_large") or images.get("urls") or []) if isinstance(images, dict) else images)
                       if isinstance(u, str)][:5],
            "attributes": attributes[:80],
        },
    }


def from_html(html: str, url: str) -> dict | None:
    """Données d'une page Leboncoin collée ou téléchargée, avant le nettoyage qui retire les scripts."""
    if not html or "__NEXT_DATA__" not in html:
        return None
    match = re.search(r'<script[^>]*\bid=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>', html, re.S | re.I)
    if not match:
        return None
    try:
        ad = json.loads(match[1])["props"]["pageProps"]["ad"]
    except (ValueError, KeyError, TypeError):
        return None
    return reduce_ad(ad) if isinstance(ad, dict) else None


def _attribute_map(ad):
    return {item["key"]: item for item in ad.get("attributes") or [] if isinstance(item, dict) and item.get("key")}


def _label(item):
    return str(item.get("value_label") or item.get("value") or "")


def _quote(item):
    names = {"immo_sell_type": "Type de vente", "type_real_estate_sale": "Type de vente"}
    return f"Leboncoin · {item.get('key_label') or names.get(item.get('key')) or item.get('key')} = {_label(item) or item.get('values')}"


def _yes_no(item):
    value, label = str(item.get("value") or ""), fold(_label(item))
    if value == "1" or label in {"oui", "yes"}:
        return "Oui"
    if value == "2" or label in {"non", "no"}:
        return "Non"
    return None


def transaction_of(ad) -> str | None:
    attributes = _attribute_map(ad)
    lease = fold((attributes.get("lease_type") or {}).get("value") or "")
    category = str(ad.get("category_id") or "")
    name = fold(ad.get("category_name") or "")
    if lease in {"rent", "let", "location"} or category in {"10", "11", "12"} or name.startswith(("locations", "colocations")):
        return "Location"
    if lease in {"sell", "sale", "vente"} or category == "9" or name.startswith("ventes immobilieres"):
        return "Achat"
    return None


def extract(site_data: dict | None, url: str) -> dict:
    """Candidats (champ, valeur, citation) issus de l'annonce Leboncoin, ou un avertissement."""
    result = {"candidates": [], "warnings": [], "title": "", "body": "", "images": [], "used": False, "hints": {}}
    if not isinstance(site_data, dict) or site_data.get("source") != SOURCE or not isinstance(site_data.get("ad"), dict):
        return result
    ad = site_data["ad"]
    expected = ad_id(url)
    if expected and str(ad.get("list_id") or "") != expected:
        result["warnings"].append("Les données Leboncoin reçues concernent une autre annonce (navigation interne du site). "
                                  "Recharge la page de l'annonce (F5) puis recapture-la.")
        return result
    result["used"] = True
    add = lambda field, value, quote: value not in (None, "") and result["candidates"].append((field, value, str(quote)[:500]))
    attributes = _attribute_map(ad)
    transaction = transaction_of(ad)
    category = ad.get("category_name") or ad.get("category_id")
    if transaction:
        add("transaction", transaction, f"Leboncoin · catégorie {category}")
    else:
        result["warnings"].append(f"Catégorie Leboncoin « {category} » : ni vente ni location de logement.")
    if str(ad.get("category_id")) == "12" or fold(ad.get("category_name") or "").startswith("locations saisonnieres"):
        result["warnings"].append("Location saisonnière : le prix affiché est souvent un prix par nuit ou par semaine.")

    prices = [p for p in ad.get("price") or [] if isinstance(p, (int, float))]
    price = prices[0] if prices else (ad["price_cents"] / 100 if isinstance(ad.get("price_cents"), (int, float)) else None)
    if price is not None:
        add("rent_monthly" if transaction == "Location" else "price_eur", float(price), f"Leboncoin · prix = {price:g} €")

    location = ad.get("location") or {}
    add("city", (location.get("city") or "").strip() or None, f"Leboncoin · ville = {location.get('city_label') or location.get('city')}")
    zipcode = str(location.get("zipcode") or "")
    if re.fullmatch(r"\d{5}", zipcode):
        add("postal_code", zipcode, f"Leboncoin · code postal = {zipcode}")
    if location.get("district"):
        add("district", str(location["district"]).strip(), f"Leboncoin · quartier = {location['district']}")

    def attr(key):
        return attributes.get(key) or {}

    area = number(attr("square").get("value"))
    if area:
        add("area_m2", area, _quote(attr("square")))
    rooms = number(attr("rooms").get("value"))
    if rooms and rooms.is_integer() and 1 <= rooms <= 20:
        add("rooms", f"T{int(rooms)}", _quote(attr("rooms")))
    for key, field in (("energy_rate", "dpe"), ("ges", "ges")):
        letter = str(attr(key).get("value") or "").strip().upper()
        if re.fullmatch(r"[A-G]", letter):
            add(field, letter, _quote(attr(key)))
    kind = fold(" ".join(str(attr(k).get("value") or "") for k in ("immo_sell_type", "type_real_estate_sale")))
    if re.search(r"\b(?:new|neuf)\b", kind):
        add("property_type", "Neuf", _quote(attr("immo_sell_type") or attr("type_real_estate_sale")))
    elif re.search(r"\b(?:old|ancien)\b", kind):
        add("property_type", "Ancien", _quote(attr("immo_sell_type") or attr("type_real_estate_sale")))
    if "viager" in kind:
        result["warnings"].append("Vente en viager : le prix affiché est un bouquet, pas un prix de vente classique.")
    estate_type = fold(_label(attr("real_estate_type")))
    if estate_type and estate_type not in {"appartement", "maison"}:
        result["warnings"].append(f"Type de bien Leboncoin : {_label(attr('real_estate_type'))}.")
    floor = attr("floor_number").get("value")
    if floor not in (None, ""):
        add("floor", "RDC" if str(floor) == "0" else str(floor), _quote(attr("floor_number")))
    year = number(attr("building_year").get("value"))
    if year and year.is_integer() and 1700 <= year <= 2100:
        add("delivery", str(int(year)), _quote(attr("building_year")))
    elif attr("available_date").get("value"):
        add("delivery", _label(attr("available_date")), _quote(attr("available_date")))

    if transaction == "Location":
        charges = number(attr("monthly_charges").get("value"))
        if charges is not None:
            add("rent_charges_monthly", charges, _quote(attr("monthly_charges")))
        included = _yes_no(attr("charges_included")) if attr("charges_included") else None
        if included:
            add("rent_charges_included", included, _quote(attr("charges_included")))
        furnished = attr("furnished")
        if furnished:
            label = fold(_label(furnished))
            value = "Non meublé" if str(furnished.get("value")) == "2" or label.startswith("non") else \
                    "Meublé" if str(furnished.get("value")) == "1" or label.startswith("meuble") else None
            add("furnished", value, _quote(furnished))
    else:
        charges = number(attr("annual_charges").get("value"))
        if charges is not None:
            add("charges_annual", charges, _quote(attr("annual_charges")))
        elif number(attr("monthly_charges").get("value")) is not None:
            add("charges_annual", number(attr("monthly_charges")["value"]) * 12, _quote(attr("monthly_charges")) + " × 12")
        if _yes_no(attr("fai_included")) == "Non" and ad.get("owner_type") == "pro" and "neuf" not in kind and "new" not in kind:
            result["warnings"].append("Leboncoin indique un prix hors honoraires d'agence : ajoute-les s'ils sont à ta charge.")

    # Attributs reconnus par leur libellé (noms de clés non documentés).
    for item in attributes.values():
        label, value = fold(item.get("key_label") or ""), number(item.get("value"))
        if value is None:
            continue
        if "taxe fonciere" in label and transaction != "Location":
            add("tax_annual", value, _quote(item))
        elif label.startswith("consommation") and "kwh" in fold(_label(item)) + label and value <= 2000:
            add("dpe_kwh_m2", value, _quote(item))
        elif label.startswith(("emission", "emissions")) and value <= 500:
            add("ges_kg_m2", value, _quote(item))

    # Indices pour retrouver l'adresse dans la base DPE (classement uniquement, jamais recopiés dans la fiche).
    lat, lng = location.get("lat"), location.get("lng")
    if isinstance(lat, (int, float)) and isinstance(lng, (int, float)):
        result["hints"].update(lat=float(lat), lng=float(lng), geo_precise=location.get("source") == "address")
    published = re.match(r"\d{4}-\d{2}-\d{2}", str(ad.get("first_publication_date") or ""))
    if published:
        result["hints"]["published"] = published[0]
    updated = re.match(r"\d{4}-\d{2}-\d{2}", str(ad.get("index_date") or ""))
    if updated:
        result["hints"]["updated"] = updated[0]
    levels = number(attr("nb_floors_building").get("value"))
    if levels is not None and levels.is_integer():
        result["hints"]["floors_building"] = int(levels)
    if estate_type in {"appartement", "maison"}:
        result["hints"]["kind"] = estate_type
    result["title"] = str(ad.get("subject") or "")
    result["body"] = str(ad.get("body") or "")
    result["images"] = [u for u in ad.get("images") or [] if isinstance(u, str) and u.startswith("https://")][:5]
    return result
