"""Repères sur une annonce, au-delà de ses champs : depuis quand elle est en ligne, comment son prix a évolué
d'une capture à l'autre, ce que son DPE implique pour la location, et si le même bien apparaît dans une autre
annonce enregistrée.

Tout est calculé localement. Les dates viennent de la capture (données du site, JSON-LD, texte de la page) ;
les prix, des captures successives de la même annonce. Repère ne surveille pas les sites : un changement de prix
n'est connu que si l'annonce est capturée de nouveau.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from bs4 import BeautifulSoup
from sqlalchemy import select

from . import market
from .db import CaptureRecord, Listing, PricePoint, Session, Setting, now
from .parsing import sites
from .parsing.rules import fold

MONTHS = {"janvier": 1, "fevrier": 2, "mars": 3, "avril": 4, "mai": 5, "juin": 6, "juillet": 7, "aout": 8,
          "septembre": 9, "octobre": 10, "novembre": 11, "decembre": 12}
# Texte replié (minuscules, sans accents) : « Publiée le », « Mise en ligne le », « Date de publication : »…
_WORD = r"(?:date de (?:publication|mise en ligne)|publiee?s?|mises? en ligne|mis en ligne|deposee?s?|en ligne depuis)"
_LINK = r"\s*(?:le|:|depuis le)?\s*"
_NUMERIC = re.compile(_WORD + _LINK + r"(\d{1,2})\s*[/.-]\s*(\d{1,2})\s*[/.-]\s*(\d{4}|\d{2})\b")
_LITERAL = re.compile(_WORD + _LINK + r"(\d{1,2})(?:er)?\s+(" + "|".join(MONTHS) + r")\s+(\d{4})\b")
_RELATIVE = re.compile(_WORD + r"\s*(?:il y a|depuis)?\s*(plus d['e ]\s*)?(\d{1,3}|un|une)\s*"
                       r"(minutes?|heures?|h|jours?|j|semaines?|mois|ans?)\b")
_DAYWORD = re.compile(_WORD + r"\s*(aujourd'?hui|avant-hier|hier)\b")
_UPDATED = re.compile(r"(?:mise? a jour|modifiee?|actualisee?|derniere modification)" + _LINK
                      + r"(\d{1,2})\s*[/.-]\s*(\d{1,2})\s*[/.-]\s*(\d{4}|\d{2})\b")
_DATE_POSTED = (re.compile(r'"datePosted"\s*:\s*"(\d{4}-\d{2}-\d{2})'),
                re.compile(r'itemprop=["\']datePosted["\'][^>]*?content=["\'](\d{4}-\d{2}-\d{2})'),
                re.compile(r'content=["\'](\d{4}-\d{2}-\d{2})[^"\']*["\'][^>]*?itemprop=["\']datePosted["\']'))
# Loi Climat et résilience : logements interdits à la location (nouveau bail, renouvellement, reconduction).
RENTAL_BAN = {"G": (2025, 2028), "F": (2028, 2031), "E": (2034, 2034)}      # (métropole, outre-mer)
BACKFILL = "insights_backfill"


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _iso_day(value) -> date | None:
    match = re.match(r"\s*(\d{4})-(\d{2})-(\d{2})", str(value or ""))
    try:
        return date(int(match[1]), int(match[2]), int(match[3])) if match else None
    except ValueError:
        return None


def _day(year, month, day) -> date | None:
    try:
        return date(int(year) + (2000 if len(str(year)) == 2 else 0), int(month), int(day))
    except ValueError:
        return None


def _html_text(html: str) -> str:
    return BeautifulSoup(html, "lxml").get_text("\n", strip=True)[:200000] if html else ""


def _text_dates(text: str, observed: date):
    """(publication, approximative, citation), (mise à jour, citation) trouvées dans le texte de la page."""
    folded = re.sub(r"\s+", " ", fold(text.replace("’", "'")))[:200000]
    found = []
    for m in _NUMERIC.finditer(folded):
        found.append((m.start(), _day(m[3], m[2], m[1]), False, m[0]))
    for m in _LITERAL.finditer(folded):
        found.append((m.start(), _day(m[3], MONTHS[m[2]], m[1]), False, m[0]))
    for m in _RELATIVE.finditer(folded):
        count = 1 if m[2] in ("un", "une") else int(m[2])
        unit = m[3]
        days = 0 if unit[0] in "mh" and not unit.startswith("mois") else \
            count * (30 if unit == "mois" else 365 if unit.startswith("an") else 7 if unit.startswith("semaine") else 1)
        found.append((m.start(), observed - timedelta(days=days), bool(m[1]) or unit == "mois" or unit.startswith("an"), m[0]))
    for m in _DAYWORD.finditer(folded):
        found.append((m.start(), observed - timedelta(days={"hier": 1, "avant-hier": 2}.get(m[1], 0)), False, m[0]))
    plausible = sorted(item for item in found if item[1] and date(2000, 1, 1) <= item[1] <= observed + timedelta(days=1))
    updated = next(((d, m[0]) for m in _UPDATED.finditer(folded)
                    if (d := _day(m[3], m[2], m[1])) and date(2000, 1, 1) <= d <= observed + timedelta(days=1)), None)
    return (plausible[0][1:] if plausible else None), updated


def extract_dates(payload: dict, observed_at: datetime) -> dict:
    """Date de première publication (et de dernière modification) lue dans une capture.
    Ordre de confiance : données du site (Leboncoin), puis datePosted (JSON-LD, microdonnées), puis texte."""
    observed = _utc(observed_at).date()
    result = {"published": None, "published_source": None, "published_quote": None, "approximate": False,
              "updated": None, "updated_source": None}
    valid = lambda d: d is not None and date(2000, 1, 1) <= d <= observed + timedelta(days=1)
    site = sites.extract(payload.get("site_data"), payload.get("url") or "")
    hints = site["hints"] if site.get("used") else {}
    published = _iso_day(hints.get("published"))
    if valid(published):
        result.update(published=published.isoformat(), published_source="Leboncoin · première publication",
                      published_quote="first_publication_date = " + str(hints["published"]))
    updated = _iso_day(hints.get("updated"))
    if valid(updated) and (published is None or updated > published):
        result.update(updated=updated.isoformat(), updated_source="Leboncoin · modification ou remontée de l'annonce")
    if not result["published"]:
        structured = "\n".join(payload.get("json_ld") or []) + "\n" + (payload.get("html") or "")
        for pattern in _DATE_POSTED:
            match = pattern.search(structured)
            if match and valid(_iso_day(match[1])):
                result.update(published=match[1], published_source="Données structurées · datePosted", published_quote=match[0][:200])
                break
    if not result["published"] or not result["updated"]:
        text_published, text_updated = _text_dates(payload.get("text") or _html_text(payload.get("html") or ""), observed)
        if text_published and not result["published"]:
            day, approximate, quote = text_published
            result.update(published=day.isoformat(), approximate=approximate, published_source="Texte de l'annonce",
                          published_quote=quote[:200])
        if text_updated and not result["updated"] and (not result["published"] or text_updated[0].isoformat() > result["published"]):
            result.update(updated=text_updated[0].isoformat(), updated_source="Texte de l'annonce")
    return result


def property_kind(title: str, hints: dict | None = None) -> str:
    """« appartement », « maison » ou "" : catégorie du site si elle existe, sinon le titre."""
    if (hints or {}).get("kind") in ("appartement", "maison"):
        return hints["kind"]
    if re.search(r"\b(?:maison|villa|pavillon)\b", title or "", re.I):
        return "maison"
    if re.search(r"\b(?:appartement|appt|studio|duplex|loft|t\d|f\d)\b", title or "", re.I):
        return "appartement"
    return ""


def listing_kind(row) -> str:
    return (row.report or {}).get("kind") or property_kind(row.title)


def observed_price(facts: dict | None):
    """(montant, projet) relevé par une capture : prix de vente, ou loyer affiché pour une location."""
    facts = facts or {}
    transaction = "Location" if facts.get("transaction") == "Location" else "Achat"
    amount = facts.get("rent_monthly" if transaction == "Location" else "price_eur")
    if isinstance(amount, (int, float)) and not isinstance(amount, bool) and amount > 0:
        return float(amount), transaction
    return None


def record_price(db, listing_id: str, facts: dict, observed_at: datetime, source: str = "capture") -> None:
    found = observed_price(facts)
    if found:
        db.add(PricePoint(listing_id=listing_id, observed_at=observed_at, amount=found[0], transaction=found[1], source=source))


def capture_signature(payload: dict) -> str:
    """Empreinte du contenu capturé : une réanalyse réutilise la même capture et n'est pas un nouveau relevé."""
    data = {k: payload.get(k) for k in ("url", "text", "site_data", "json_ld", "selection_only")}
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def history_from_captures(records, skip=frozenset()):
    """Relevés de prix et repères de la dernière capture, à partir de (fiche, date, contenu, rapport) triés
    par fiche puis par date. Les fiches de `skip` ont déjà un historique : seuls leurs repères sont calculés."""
    points, latest, signatures = [], {}, {}
    for listing_id, created_at, payload, report in records:
        payload = payload or {}
        signature = capture_signature(payload)
        if listing_id not in skip and signatures.get(listing_id) != signature:
            found = observed_price((report or {}).get("facts"))
            if found:
                points.append(PricePoint(listing_id=listing_id, observed_at=created_at, amount=found[0],
                                         transaction=found[1], source="capture"))
        signatures[listing_id] = signature
        hints = sites.extract(payload.get("site_data"), payload.get("url") or "")["hints"]
        latest[listing_id] = {"dates": extract_dates(payload, created_at), "hints": hints}
    return points, latest


def complete_reports(db, latest: dict, ids=None) -> None:
    """Ajoute dates et type de bien aux rapports des fiches enregistrées avant leur apparition."""
    query = select(Listing) if ids is None else select(Listing).where(Listing.id.in_(list(ids)))
    for row in db.scalars(query):
        report, info = dict(row.report or {}), latest.get(row.id)
        changed = False
        if "dates" not in report and info:
            report["dates"], changed = info["dates"], True
        if "kind" not in report:
            report["kind"], changed = property_kind(row.title, info["hints"] if info else {}), True
        if changed:
            row.report = report


def backfill() -> None:
    """Une seule fois, au démarrage : relevés de prix tirés des captures existantes, dates et type de bien."""
    with Session.begin() as db:
        if db.get(Setting, BACKFILL):
            return
        skip = {listing_id for (listing_id,) in db.execute(select(PricePoint.listing_id).distinct())}
        records = db.execute(select(CaptureRecord.listing_id, CaptureRecord.created_at, CaptureRecord.payload,
                                    CaptureRecord.report).order_by(CaptureRecord.listing_id, CaptureRecord.created_at))
        points, latest = history_from_captures(records, frozenset(skip))
        db.add_all(points)
        complete_reports(db, latest)
        db.add(Setting(key=BACKFILL, value={"version": 1, "at": now().isoformat(), "points": len(points)}))


def price_history(points, transaction: str) -> dict | None:
    rows = sorted((p for p in points if p.transaction == transaction), key=lambda p: _utc(p.observed_at))
    if not rows:
        return None
    first, last = rows[0], rows[-1]
    changes = [{"at": _utc(b.observed_at).isoformat(), "from": a.amount, "to": b.amount}
               for a, b in zip(rows, rows[1:]) if abs(b.amount - a.amount) >= 1]
    change = last.amount - first.amount
    return {"first": {"amount": first.amount, "at": _utc(first.observed_at).isoformat()},
            "current": {"amount": last.amount, "at": _utc(last.observed_at).isoformat()},
            "change_eur": round(change, 2) if abs(change) >= 1 else 0,
            "change_pct": round(change / first.amount * 100, 1) if abs(change) >= 1 else 0,
            "changes": changes[-10:], "observations": len(rows),
            "points": [{"id": p.id, "amount": p.amount, "at": _utc(p.observed_at).isoformat(), "source": p.source}
                       for p in rows][-30:]}


def online(row, points, today: date) -> dict:
    """Ancienneté : date de publication (saisie, ou lue dans la capture) et date du premier relevé dans Repère."""
    dates = (row.report or {}).get("dates") or {}
    manual = (row.overrides or {}).get("published_on")
    published = manual or dates.get("published")
    first_seen = min([_utc(row.created_at)] + [_utc(p.observed_at) for p in points]).date()
    day = _iso_day(published)
    return {"published": day.isoformat() if day else None,
            "source": "Date saisie dans la fiche" if manual else dates.get("published_source"),
            "quote": None if manual else dates.get("published_quote"),
            "approximate": False if manual else bool(dates.get("approximate")), "manual": bool(manual),
            "days": max(0, (today - day).days) if day else None,
            "updated": dates.get("updated"), "updated_source": dates.get("updated_source"),
            "first_seen": first_seen.isoformat(), "days_followed": max(0, (today - first_seen).days)}


def dpe_rental(facts: dict, today: date) -> dict | None:
    dpe = (facts or {}).get("dpe")
    if dpe not in RENTAL_BAN:
        return None
    overseas = str(facts.get("postal_code") or "").startswith("97")
    year = RENTAL_BAN[dpe][1 if overseas else 0]
    return {"dpe": dpe, "year": year, "banned": today >= date(year, 1, 1), "rent_frozen": dpe in ("F", "G"),
            "overseas": overseas}


def same_property(a: dict, b: dict) -> str | None:
    """Raison pour laquelle deux fiches décrivent probablement le même logement, sinon None."""
    if (a.get("transaction") or "Achat") != (b.get("transaction") or "Achat"):
        return None
    if a.get("postal_code") and b.get("postal_code"):
        if a["postal_code"] != b["postal_code"]:
            return None
    elif not (a.get("city") and b.get("city") and market.norm_city(a["city"]) == market.norm_city(b["city"])):
        return None
    area_a, area_b = a.get("area_m2"), b.get("area_m2")
    if not area_a or not area_b or abs(area_a - area_b) > max(1.0, 0.02 * max(area_a, area_b)):
        return None
    kwh_a, kwh_b = a.get("dpe_kwh_m2"), b.get("dpe_kwh_m2")
    if kwh_a is not None and kwh_b is not None:
        return "même surface et même consommation DPE" if abs(kwh_a - kwh_b) <= 1 else None
    letters = a.get("dpe") not in (None, "NC") and a.get("dpe") == b.get("dpe")
    ges_a, ges_b = a.get("ges_kg_m2"), b.get("ges_kg_m2")
    if letters and ges_a is not None and ges_b is not None:
        return "même surface, même DPE et mêmes émissions de GES" if abs(ges_a - ges_b) <= 0.5 else None
    floors = not (a.get("floor") and b.get("floor")) or str(a["floor"]).strip().casefold() == str(b["floor"]).strip().casefold()
    if letters and a.get("ges") not in (None, "NC") and a.get("ges") == b.get("ges") and a.get("rooms") \
            and a.get("rooms") == b.get("rooms") and floors:
        return "même surface, mêmes pièces, DPE et GES"
    return None


def same_property_index(rows) -> dict:
    """{fiche: [fiches du même bien probable]} ; comparaison limitée aux surfaces voisines (fenêtre glissante)."""
    entries = sorted((r for r in rows if (r.facts or {}).get("area_m2")), key=lambda r: r.facts["area_m2"])
    index = defaultdict(list)
    for i, a in enumerate(entries):
        for b in entries[i + 1:]:
            if b.facts["area_m2"] - a.facts["area_m2"] > max(1.0, 0.02 * b.facts["area_m2"]):
                break
            reason = same_property(a.facts, b.facts)
            if reason:
                index[a.id].append(_summary(b, reason))
                index[b.id].append(_summary(a, reason))
    return index


def _summary(row, reason):
    found = observed_price(row.facts)
    host = re.sub(r"^www\.", "", re.sub(r"^https?://([^/]+).*$", r"\1", row.url or "")) if row.url else None
    return {"id": row.id, "title": row.title, "host": host, "amount": found[0] if found else None,
            "created_at": _utc(row.created_at).isoformat(), "status": row.status, "reason": reason}


def build(row, points, same, today: date) -> dict:
    facts = row.facts or {}
    transaction = "Location" if facts.get("transaction") == "Location" else "Achat"
    return {"online": online(row, points, today), "price": price_history(points, transaction),
            "dpe_rental": dpe_rental(facts, today), "same_property": same,
            "market": market.compare((row.report or {}).get("market"), facts, listing_kind(row))}


def _euros(value) -> str:
    return f"{value:,.0f}".replace(",", " ") + " €"


def _fr_day(iso: str) -> str:
    day = _iso_day(iso)
    return day.strftime("%d/%m/%Y") if day else "?"


def headline(insight: dict) -> list[str]:
    """Résumé en quelques phrases (fenêtre de l'extension après une capture)."""
    lines = []
    when = insight.get("online") or {}
    if when.get("published"):
        lines.append(f"En ligne depuis {duration(when['days'])}{' environ' if when.get('approximate') else ''} "
                     f"(publiée le {_fr_day(when['published'])}).")
    price = insight.get("price")
    if price and price["observations"] > 1:
        if price["change_eur"]:
            lines.append(f"Prix relevé : {_euros(price['first']['amount'])} le {_fr_day(price['first']['at'])}, "
                         f"{_euros(price['current']['amount'])} aujourd'hui ({_pct(price['change_pct'])}).")
        else:
            lines.append(f"Prix inchangé depuis le premier relevé du {_fr_day(price['first']['at'])}.")
    elif price:
        lines.append("Premier relevé de prix enregistré : recapture l'annonce plus tard pour suivre son évolution.")
    comparison = insight.get("market") or {}
    if not comparison.get("stale"):
        sale, rent = comparison.get("sale") or {}, comparison.get("rent") or {}
        if sale.get("diff_pct") is not None:
            lines.append(f"Prix au m² : {_pct(sale['diff_pct'])} par rapport au prix médian des {sale['kind']} vendus "
                         f"à {_commune(comparison)}.")
        if rent.get("diff_pct") is not None:
            lines.append(f"Loyer au m² : {_pct(rent['diff_pct'])} par rapport au loyer de référence des {rent['kind']} "
                         f"à {_commune(comparison)}.")
    ban = insight.get("dpe_rental")
    if ban:
        lines.append(f"DPE {ban['dpe']} : location interdite {'depuis' if ban['banned'] else 'à partir de'} {ban['year']}.")
    if insight.get("same_property"):
        count = len(insight["same_property"])
        lines.append(f"Même bien probable dans {count} autre{'s' if count > 1 else ''} annonce{'s' if count > 1 else ''} de Repère.")
    return lines


def duration(days: int) -> str:
    if days < 1:
        return "aujourd'hui"
    if days < 61:
        return f"{days} jour{'s' if days > 1 else ''}"
    months = round(days / 30.4)
    return f"{months} mois" if months < 24 else f"{int(days // 365.25)} ans"


def _commune(comparison: dict) -> str:
    return re.sub(r" Arrondissement$", "", comparison.get("commune") or "") or "la commune"


def _pct(value: float) -> str:
    sign = "+" if value > 0 else "−" if value < 0 else ""
    return f"{sign}{abs(value):.1f}".replace(".", ",") + " %"
