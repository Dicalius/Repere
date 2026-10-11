from __future__ import annotations
import json
import os
import re
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit
from bs4 import BeautifulSoup
from soupsieve import SelectorSyntaxError
from pydantic import ValidationError
from ..schemas import Capture, Facts
from .rules import detect_transaction, extract_local, number, fold, RANKS
from . import sites

FIELD_MAP = {"prix_eur": "price_eur", "surface_m2": "area_m2", "ville": "city", "quartier": "district",
             "type_bien": "property_type", "pieces": "rooms", "dpe": "dpe", "annee_livraison": "delivery",
             "charges_copro_an_eur": "charges_annual", "taxe_fonciere_an_eur": "tax_annual",
             "parking_options_eur": "parking_extra", "travaux_eur": "works_eur", "distance_gare_min": "station_minutes",
             "code_postal": "postal_code", "conso_kwh_m2": "dpe_kwh_m2", "ges_kg_m2": "ges_kg_m2", "ges": "ges",
             "loyer_mois": "rent_monthly", "charges_loyer_mois": "rent_charges_monthly",
             "charges_incluses": "rent_charges_included", "meuble": "furnished"}
# Champs propres à chaque projet : ignorés (mais visibles dans les diagnostics) pour l'autre.
PURCHASE_ONLY = ("price_eur", "charges_annual", "tax_annual", "parking_extra", "works_eur")
RENT_ONLY = ("rent_monthly", "rent_charges_monthly", "rent_charges_included", "furnished")
EXCLUDED = "style,noscript,svg,nav,input,textarea,select,button,[hidden],[aria-hidden='true']"
# En-têtes, pieds et encarts ne sont retirés que hors du contenu principal : dans une annonce,
# le <header> de l'article porte souvent le titre, le prix et la ville.
LAYOUT = "header,footer,aside"
LABELS = {"price_eur": "prix", "area_m2": "surface", "city": "ville", "district": "quartier", "property_type": "neuf/ancien",
          "rooms": "pièces", "dpe": "DPE", "floor": "étage", "charges_annual": "charges", "tax_annual": "taxe foncière",
          "parking_extra": "parking", "works_eur": "travaux", "station_minutes": "gare", "delivery": "livraison",
          "postal_code": "code postal", "ges": "GES", "dpe_kwh_m2": "consommation DPE", "ges_kg_m2": "émissions GES",
          "transaction": "achat ou location", "rent_monthly": "loyer", "rent_charges_monthly": "charges locatives",
          "rent_charges_included": "charges comprises", "furnished": "meublé"}
KIND_LABELS = {"primary": "titre / résumé", "labelled": "libellé", "scan": "corps de page", "structured": ""}


def clean_html(raw: str):
    soup = BeautifulSoup(raw, "lxml")
    for tag in soup.select(EXCLUDED):
        tag.decompose()
    for tag in soup.select(LAYOUT):
        if not tag.decomposed and not tag.find_parent(["main", "article"]) and \
                not any(p.get("role") == "main" for p in tag.parents if getattr(p, "attrs", None)):
            tag.decompose()
    # Un <form> peut envelopper toute la page (ASP.NET WebForms) : on garde son contenu, sans ses champs.
    for tag in soup.find_all("form"):
        tag.unwrap()
    for tag in soup.select("script"):
        if tag.get("type", "").lower() != "application/ld+json":
            tag.decompose()
    for tag in soup.find_all(True):
        for attr in list(tag.attrs):
            if attr.lower().startswith("on") or attr in {"srcdoc", "value"}:
                del tag[attr]
    return str(soup)


def content_text(soup):
    clone = BeautifulSoup(str(soup), "lxml")
    for tag in clone.select("script,style,head"):
        tag.decompose()
    root = clone.find("main") or clone.find(attrs={"role": "main"}) or clone.body or clone
    return root.get_text("\n", strip=True)[:120000]


def microdata_to_ld(value):
    if isinstance(value, list):
        return [microdata_to_ld(v) for v in value]
    if not isinstance(value, dict):
        return value
    if "properties" in value and isinstance(value["properties"], dict):
        out = {"@type": value.get("type", "")}
        if value.get("id"):
            out["@id"] = value["id"]
        for key, item in value["properties"].items():
            converted = microdata_to_ld(item)
            out[key] = converted[0] if isinstance(converted, list) and len(converted) == 1 else converted
        return out
    return {key: microdata_to_ld(item) for key, item in value.items()}


def pipeline(capture: Capture):
    candidates = defaultdict(list)
    warnings = list(capture.capture_warnings)
    steps = []
    metadata = {} if capture.selection_only else dict(capture.metadata)
    blocks = [] if capture.selection_only else list(capture.json_ld)
    html = "" if capture.selection_only else capture.html
    soup = BeautifulSoup(clean_html(html), "lxml") if html else None
    title = capture.title
    text = capture.text or (content_text(soup) if soup else "")
    nearly_empty = bool(html and not capture.text and len(text.strip()) < 400)
    if re.search(r"verify (?:that )?you are human|vérifiez que vous êtes humain|checking your browser|enable js and disable", text, re.I):
        warnings.append("La capture semble contenir une page de vérification. Ouvre l'annonce complète puis recapture-la.")
        return {"title": title, "facts": Facts().model_dump(), "candidates": {}, "conflicts": {},
                "warnings": warnings, "steps": [{"name": "Page de vérification", "candidates": 0, "error": True}],
                "missing": ["price_eur", "area_m2", "city"], "parser_version": "1.2"}
    if soup:
        heading = soup.title or soup.find("h1")
        title = title or (heading.get_text(" ", strip=True)[:1000] if heading else "")
        for tag in soup.select("meta[content]"):
            name = tag.get("property", tag.get("name", ""))
            if name in {"og:title", "description", "og:description", "product:price:amount", "product:price:currency", "og:price:amount", "og:price:currency", "og:image", "twitter:image"}:
                metadata.setdefault(name, tag["content"][:3000])

    def add(field, value, parser, quote, rank):
        if field not in Facts.model_fields or value is None or value == "":
            return
        try:
            value = Facts.model_validate({field: value}).model_dump()[field]
        except ValidationError:
            return
        if field == "price_eur" and value < 1000:
            return
        item = {"value": value, "parser": parser, "quote": str(quote)[:600], "rank": rank}
        if item not in candidates[field] and len(candidates[field]) < 25:
            candidates[field].append(item)

    rental = False

    def apply_rules(parser, source_text="", source_blocks=None, source_meta=None, source_title="", selection=False):
        source = SimpleNamespace(title=source_title, text=source_text, json_ld=source_blocks or [],
                                 metadata=source_meta or {}, selection_only=selection, rental=rental)
        local = extract_local("", capture.url or "https://capture.invalid/", source)
        for field, values in local.get("candidates", {}).items():
            for value, quote, kind in values:
                detail = "sélection" if capture.selection_only and kind == "primary" else KIND_LABELS.get(kind, "")
                add(FIELD_MAP.get(field, field), value, parser + (" · " + detail if detail else ""), quote, RANKS[kind])
        warnings.extend(local["warnings"])

    # 1. Données publiées par le site lui-même (Leboncoin) : rang le plus fiable, y compris en mode sélection.
    site_data = capture.site_data
    if site_data is None and html:
        site_data = sites.site_data_from_html(html, capture.url)
    site = sites.extract(site_data, capture.url)
    warnings.extend(site["warnings"])
    if site["used"]:
        for field, value, quote in site["candidates"]:
            add(field, value, site["parser"], quote, RANKS["site"])
        title = site["title"] or title
        steps.append({"name": site["parser"], "candidates": sum(map(len, candidates.values()))})

    if nearly_empty and not site["used"]:
        warnings.append("La page téléchargée est presque vide (site qui s'affiche en JavaScript ou protégé). "
                        "Ouvre l'annonce dans ton navigateur et utilise l'extension.")

    # 2. Achat ou location, avant les règles de prix : un loyer ne doit jamais devenir un prix d'achat.
    primary = [title, *(metadata.get(k, "") for k in ("og:title", "description", "og:description"))]
    for value, quote, kind in detect_transaction(capture.url, primary, text + "\n" + site["body"]):
        add("transaction", value, "Achat ou location · " + {"structured": "URL", "primary": "titre", "scan": "vocabulaire"}[kind],
            quote, RANKS[kind])
    if candidates.get("transaction"):
        best = min(item["rank"] for item in candidates["transaction"])
        rental = {item["value"] for item in candidates["transaction"] if item["rank"] == best} == {"Location"}

    if site["used"] and site["body"] and "text" in capture.parsers:
        apply_rules(site["parser"].split(" · ")[0] + " · description", source_text=site["body"])

    if "structured" in capture.parsers and not capture.selection_only:
        before = sum(map(len, candidates.values()))
        if soup:
            try:
                import extruct
                structured = extruct.extract(str(soup), base_url=capture.url or "https://capture.invalid/",
                                            syntaxes=["json-ld", "microdata"], errors="ignore")
                blocks += [json.dumps(item, ensure_ascii=False) for item in structured.get("json-ld", [])]
                micro = [json.dumps(microdata_to_ld(item), ensure_ascii=False) for item in structured.get("microdata", [])]
                apply_rules("Microdonnées · extruct", source_blocks=micro)
            except Exception as exc:
                warnings.append("Métadonnées HTML non décodées : " + type(exc).__name__)
        blocks = list(dict.fromkeys(blocks))
        apply_rules("JSON-LD", source_blocks=blocks)
        apply_rules("Métadonnées", source_title=title, source_meta=metadata)
        property_title = fold(title + " " + metadata.get("og:title", ""))
        if not rental and re.search(r"appartement|maison|studio|logement", property_title) and not re.search(r"location|a louer", property_title):
            for prefix in ("product:price", "og:price"):
                amount, currency = metadata.get(prefix + ":amount"), metadata.get(prefix + ":currency", "")
                if currency.upper() in {"EUR", "€"}:
                    add("price_eur", number(amount), "Métadonnées prix", f"{prefix}:amount={amount}; currency={currency}", RANKS["structured"])
        steps.append({"name": "Métadonnées structurées", "candidates": sum(map(len, candidates.values())) - before})

    if "text" in capture.parsers:
        before = sum(map(len, candidates.values()))
        apply_rules("Texte visible", source_text=text, source_title=title, selection=capture.selection_only)
        steps.append({"name": "Texte visible", "candidates": sum(map(len, candidates.values())) - before})

    if soup and "html" in capture.parsers:
        before = sum(map(len, candidates.values()))
        root = soup.find("main") or soup.find(attrs={"role": "main"}) or soup.body or soup
        html_rental = rental or bool(re.search(r"\b(?:location|a louer)\b", fold(title)))
        selectors = {"price_eur": ".price,.prix,.ad-price,.listing-price,[data-testid*='price']",
                     "area_m2": ".surface,[data-testid*='surface'],[data-testid*='area']",
                     "city": "[itemprop='addressLocality'],.city,.ville",
                     "dpe": "[data-testid='dpe'],[data-dpe]"}

        def read_element(field, el, parser, unit="", rank=RANKS["labelled"]):
            raw = el.get("content") or el.get("data-dpe") or el.get_text(" ", strip=True)
            if not raw or len(raw) > 250:
                return
            folded = fold(raw)
            nearby = fold((el.parent.get_text(" ", strip=True) if el.parent else raw)[:500])
            if field in {"price_eur", "area_m2", "charges_annual", "tax_annual", "works_eur", "parking_extra"}:
                pattern = r"(\d{1,3}(?:[ \u00a0\u202f]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?)"
                numeric_raw = re.sub(r"m(?:2|²)", "", raw, flags=re.I)
                amounts = re.findall(pattern, numeric_raw)
                if len(amounts) != 1:
                    return
                if field == "price_eur" and (html_rental or re.search(r"a partir|loyer|mensual|/\s*(?:mois|m2)|honoraires", folded)
                                           or re.search(r"loyer|mensualite|simulateur|simulation de credit", nearby)):
                    return
                if field == "area_m2" and (re.search(r"terrain|jardin|terrasse|balcon", folded) or not (unit == "m2" or re.search(r"m(?:²|2)", raw))):
                    return
                if field != "area_m2" and not (unit == "EUR" or re.search(r"€|euros?|EUR", raw)):
                    return
                add(field, number(amounts[0]), parser, raw + (f" [{unit}]" if unit else ""), rank)
            elif field == "dpe":
                if re.fullmatch(r"[A-G]", raw.strip(), re.I):
                    add(field, raw.strip().upper(), parser, raw, rank)
            else:
                add(field, raw.strip(), parser, raw, rank)

        for field, selector in selectors.items():
            for el in root.select(selector)[:30]:
                if any(re.search(r"recommend|similar|suggest|related", " ".join(p.get("class", [])), re.I) for p in [el, *el.parents] if getattr(p, "attrs", None)):
                    continue
                if any(re.search(r"RealEstateAgent|Organization|Person", str(p.get("itemtype", ""))) for p in el.parents if getattr(p, "attrs", None)):
                    continue
                read_element(field, el, "HTML · sélecteur")
        config = Path(os.getenv("PARSER_RULES_PATH", "config/parsers.json"))
        if config.exists():
            try:
                for adapter in json.loads(config.read_text()).get("sites", []):
                    if (urlsplit(capture.url).hostname or "").removeprefix("www.") not in adapter.get("hosts", []):
                        continue
                    for field, rule in adapter.get("fields", {}).items():
                        for el in root.select(rule["selector"])[:20]:
                            read_element(field, el, "Règle du site", rule.get("unit", ""), RANKS["site"])
            except (ValueError, KeyError, TypeError, OSError, SelectorSyntaxError) as exc:
                warnings.append("Règles du site non appliquées : " + type(exc).__name__)
        steps.append({"name": "Analyse HTML", "candidates": sum(map(len, candidates.values())) - before})

    if soup and "trafilatura" in capture.parsers:
        before = sum(map(len, candidates.values()))
        try:
            import trafilatura
            readable = trafilatura.extract(str(soup), url=capture.url or None, include_comments=False,
                                          include_tables=True, favor_recall=True, no_fallback=False) or ""
            apply_rules("Texte principal · Trafilatura", source_text=readable[:120000])
        except Exception as exc:
            warnings.append("Extraction du texte principal indisponible : " + type(exc).__name__)
        steps.append({"name": "Texte principal · Trafilatura", "candidates": sum(map(len, candidates.values())) - before})

    ai_context = "\n".join(["TITLE: " + title, *[f"META {k}: {v}" for k, v in metadata.items()],
                              *["JSON-LD: " + b for b in blocks], "PAGE_TEXT: " + text])[:220000]
    if capture.use_gemini:
        try:
            from .gemini import extract_ai
            found, rejected = extract_ai(ai_context)
            for item in found:
                add(item["field"], item["value"], "Gemini · citation vérifiée", item["quote"], RANKS["gemini"])
            if rejected:
                warnings.append("Citations Gemini non vérifiées : " + ", ".join(rejected))
            steps.append({"name": "Gemini", "candidates": len(found)})
        except Exception as exc:
            from .gemini import safe_error
            warnings.append("Complément Gemini indisponible : " + safe_error(exc))
            steps.append({"name": "Gemini", "candidates": 0, "error": True})
    def key(item):
        value = item["value"]
        return str(round(value, 6)) if isinstance(value, (float, int)) else str(value).strip().casefold()

    data, conflicts, overruled, arbitrated, disputed = {}, {}, [], [], []
    for field, values in candidates.items():
        label = LABELS.get(field, field)
        best = min(item["rank"] for item in values)
        top_keys = {key(item) for item in values if item["rank"] == best}
        # Valeurs de Gemini dont la citation a été retrouvée dans la capture : un avis indépendant des règles.
        ai_keys = {key(item) for item in values if item["rank"] == RANKS["gemini"]}
        chosen = None
        if len(top_keys) == 1:
            chosen = next(iter(top_keys))
            if best in (RANKS["labelled"], RANKS["scan"]) and ai_keys and chosen not in ai_keys:
                chosen = None            # règle simple contredite par Gemini : l'utilisateur tranche
                disputed.append(label)
        elif len(top_keys & ai_keys) == 1:
            chosen = next(iter(top_keys & ai_keys))  # Gemini confirme une des valeurs en conflit
            arbitrated.append(label)
        if chosen is None:
            conflicts[field] = values
            continue
        data[field] = next(item["value"] for item in values if key(item) == chosen)
        for item in values:
            item["retained"] = key(item) == chosen
        if len({key(item) for item in values}) > 1 and label not in arbitrated:
            overruled.append(label)
    transaction = data.get("transaction", "Achat")
    ignored = [f for f in (RENT_ONLY if transaction == "Achat" else PURCHASE_ONLY) if f in data or f in conflicts]
    for field in ignored:
        data.pop(field, None)
        conflicts.pop(field, None)
    if transaction == "Location" and ignored:
        warnings.append("Location : prix d'achat, charges de copropriété et taxe foncière ne sont pas repris dans la fiche.")
    if "transaction" not in data and "transaction" not in conflicts:
        warnings.append("Achat ou location non précisé par l'annonce : fiche classée en achat. Change-le dans la fiche si besoin.")
    if conflicts:
        warnings.append("Valeurs contradictoires laissées vides : " + ", ".join(LABELS.get(f, f) for f in conflicts)
                        + ". Choisis la bonne valeur ci-dessous avec « Utiliser ».")
    if arbitrated:
        warnings.append("Conflit tranché par Gemini (valeur confirmée par une citation de l'annonce) : " + ", ".join(arbitrated) + ".")
    if disputed:
        warnings.append("Gemini ne confirme pas la valeur trouvée par les règles locales : " + ", ".join(disputed) + ".")
    if overruled:
        warnings.append("Valeur retenue d'après la source la plus fiable, autres valeurs écartées : " + ", ".join(overruled)
                        + ". Vérifie-les dans la liste ci-dessous.")
    if capture.selection_only:
        warnings.append("Sélection manuelle : analyse du texte sélectionné uniquement.")
    return {"title": title, "facts": Facts.model_validate(data).model_dump(), "candidates": dict(candidates),
            "conflicts": conflicts, "warnings": list(dict.fromkeys(warnings)), "steps": steps,
            "missing": [f for f in ("rent_monthly" if transaction == "Location" else "price_eur", "area_m2", "city") if not data.get(f)],
            "site": site["parser"] if site["used"] else None, "parser_version": "1.2"}
