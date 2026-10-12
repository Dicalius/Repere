from __future__ import annotations
import json
import hashlib
import os
import re
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from uuid import UUID
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select, delete, func
from sqlalchemy.exc import IntegrityError
from pydantic import ValidationError
from . import VERSION
from .db import DATA_DIR, Session, Listing, CaptureRecord, Photo, Setting, Scenario, initialize, now
from .schemas import AddressSearch, Capture, Facts, ImportRequest, ListingEdit, PhotoRequest, Profile, ScenarioSave, Simulation
from . import photos
from .dpe_lookup import search_addresses
from .finance import calculate
from .security import authenticate, token, public_url
from .parsing.fetch import fetch_html
from .parsing.pipeline import pipeline, clean_html
from .parsing import sites

STATIC = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app):
    initialize()
    token()
    yield


app = FastAPI(title="Repère", version=VERSION, lifespan=lifespan, docs_url=None, redoc_url=None)


@app.exception_handler(ValidationError)
async def invalid_fields(request, exc):
    return JSONResponse({"detail": [{"loc": ["body", *e["loc"]], "msg": e["msg"]}
                                    for e in exc.errors(include_input=False)]}, status_code=422)


@app.exception_handler(IntegrityError)
async def concurrent_change(request, exc):
    return JSONResponse({"detail": "Une modification concurrente a eu lieu. Actualise puis réessaie."}, status_code=409)


@app.middleware("http")
async def boundaries(request: Request, call_next):
    host = request.headers.get("host", "").split(":")[0]
    allowed = {"127.0.0.1", "localhost", *os.getenv("REPERE_ALLOWED_HOSTS", "").split(",")}
    if host not in allowed:
        return JSONResponse({"detail": "Hôte non autorisé"}, status_code=400)
    origin = request.headers.get("origin", "")
    public = os.getenv("REPERE_PUBLIC_URL", "http://127.0.0.1:8765").rstrip("/")
    if origin and origin not in {public, "http://localhost:8765", "http://127.0.0.1:8765"} and not re.fullmatch(r"chrome-extension://[a-p]{32}", origin):
        return JSONResponse({"detail": "Origine non autorisée"}, status_code=403)
    if request.method in {"POST", "PUT", "PATCH"}:
        # Vérification sur les octets reçus, y compris sans Content-Length.
        limit = 20_000_000 if request.url.path == "/api/restore" else 2_500_000
        content = bytearray()
        async for chunk in request.stream():
            content.extend(chunk)
            if len(content) > limit:
                return JSONResponse({"detail": "Contenu trop volumineux"}, status_code=413)
        request._body = bytes(content)
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'"
    return response


def photo_versions(db, ids=None):
    query = select(Photo.listing_id, Photo.digest)
    if ids is not None:
        query = query.where(Photo.listing_id.in_(ids))
    return {listing_id: value[:12] for listing_id, value in db.execute(query)}


def listing_json(row, photo=None):
    return {"id": row.id, "url": row.url, "title": row.title, "facts": row.facts, "photo": photo,
            "overrides": row.overrides, "report": row.report, "notes": row.notes, "status": row.status,
            "favorite": row.favorite, "reviewed": row.reviewed, "revision": row.revision,
            "created_at": row.created_at.isoformat(), "updated_at": row.updated_at.isoformat()}


def require_listing(db, ident, lock=False):
    row = db.get(Listing, ident, with_for_update=True if lock else None)
    if not row:
        raise HTTPException(404, "Annonce introuvable")
    return row


def prepare_site_data(capture):
    """Avant le nettoyage du HTML (qui retire les scripts) : données propres au site et photos de l'annonce."""
    if capture.site_data is None and capture.html:
        capture.site_data = sites.site_data_from_html(capture.html, capture.url)
    images = sites.extract(capture.site_data, capture.url)["images"] if capture.site_data else []
    if images:
        capture.images = list(dict.fromkeys(images + capture.images))[:5]


def store_capture(capture, existing_id=None):
    if capture.url:
        capture.url = public_url(capture.url)
    prepare_site_data(capture)
    if capture.selection_only:
        capture.html, capture.json_ld, capture.metadata = "", [], {}
    elif capture.html:
        capture.html = clean_html(capture.html)
    if not capture.text.strip() and not capture.html.strip() and not capture.json_ld:
        raise HTTPException(400, "Ajoute du texte, du HTML ou une capture de l'annonce.")
    report = pipeline(capture)
    with Session.begin() as db:
        row = require_listing(db, existing_id, lock=True) if existing_id else db.scalar(select(Listing).where(Listing.url == capture.url).with_for_update()) if capture.url else None
        if row is None:
            row = Listing(url=capture.url or None, url_hash=hashlib.sha256(capture.url.encode()).hexdigest() if capture.url else None,
                          title=report["title"] or "Annonce à compléter",
                          facts=report["facts"], overrides={}, report=report, reviewed=False)
            db.add(row)
            db.flush()
        else:
            row.facts = {**report["facts"], **{k: v for k, v in row.overrides.items() if k in Facts.model_fields}}
            if "title" not in row.overrides:
                row.title = report["title"] or row.title
            row.report, row.reviewed = report, False
            row.updated_at, row.revision = now(), row.revision + 1
        db.add(CaptureRecord(listing_id=row.id, payload=capture.model_dump(), report=report))
        db.flush()
        result = listing_json(row)
    # Téléchargement hors transaction : une photo lente ou absente ne bloque jamais l'enregistrement.
    if result["id"] and not has_photo(result["id"]):
        try:
            save_photo(result["id"], *photos.fetch_photo(photos.sources(capture), capture.url))
        except HTTPException:
            pass
    found = auto_address_search(result["id"]) if result["id"] else None
    if found is not None:
        result["report"] = {**(result["report"] or {}), "address_search": found}
    return result


def has_photo(listing_id):
    with Session() as db:
        return db.get(Photo, listing_id) is not None


def save_photo(listing_id, data, source_url):
    with Session.begin() as db:
        if not db.get(Listing, listing_id):
            return None
        photo = db.get(Photo, listing_id) or Photo(listing_id=listing_id)
        photo.data, photo.digest, photo.source_url, photo.created_at = data, photos.digest(data), source_url[:2048], now()
        db.merge(photo)
        return photo.digest[:12]


@app.get("/health")
def health():
    return {"ok": True, "version": VERSION, "application": "Repère", "browser_capture": True}


@app.get("/api/status", dependencies=[Depends(authenticate)])
def status():
    with Session() as db:
        return {"version": VERSION, "gemini_configured": bool(os.getenv("GEMINI_API_KEY")),
                "model": os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite"),
                "listings": db.scalar(select(func.count()).select_from(Listing)),
                "database": "PostgreSQL" if db.bind.dialect.name == "postgresql" else "SQLite (développement)",
                "release": release_info()}


def release_info():
    """Origine du code lancé, écrite par le démarrage du conteneur (docker/entrypoint.py)."""
    try:
        state = json.loads((DATA_DIR / "release.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return {k: state.get(k) for k in ("source", "commit", "ref", "started_at")} if isinstance(state, dict) else None


@app.get("/api/listings", dependencies=[Depends(authenticate)])
def listings():
    with Session() as db:
        versions = photo_versions(db)
        return [listing_json(row, versions.get(row.id)) for row in db.scalars(select(Listing).order_by(Listing.updated_at.desc()))]


@app.post("/api/listings", dependencies=[Depends(authenticate)])
def manual_listing(edit: ListingEdit):
    facts = Facts.model_validate(edit.facts).model_dump()
    with Session.begin() as db:
        row = Listing(title=edit.title or "Annonce manuelle", facts=facts, overrides=edit.facts,
                      report={"candidates": {}, "steps": [], "warnings": [], "conflicts": {}},
                      notes=edit.notes or "", reviewed=bool(edit.reviewed))
        db.add(row)
        db.flush()
        return listing_json(row)


@app.get("/api/listings/{ident}", dependencies=[Depends(authenticate)])
def listing(ident: str):
    with Session() as db:
        return listing_json(require_listing(db, ident), photo_versions(db, [ident]).get(ident))


@app.get("/api/listings/{ident}/photo", dependencies=[Depends(authenticate)])
def listing_photo(ident: str):
    with Session() as db:
        photo = db.get(Photo, ident)
        if not photo:
            raise HTTPException(404, "Pas de photo pour cette annonce.")
        return Response(photo.data, media_type="image/jpeg")


@app.post("/api/listings/{ident}/photo", dependencies=[Depends(authenticate)])
def refresh_photo(ident: str, request: PhotoRequest):
    """Photo choisie (URL d'image) ou recherchée dans la dernière capture, puis dans la page publique."""
    with Session() as db:
        row = require_listing(db, ident)
        url = row.url
        latest = db.scalar(select(CaptureRecord).where(CaptureRecord.listing_id == ident).order_by(CaptureRecord.created_at.desc()))
        capture = Capture.model_validate(latest.payload) if latest else None
    if request.url:
        data, source = photos.fetch_photo([request.url.strip()], url or "")
    else:
        candidates = photos.sources(capture) if capture else []
        try:
            data, source = photos.fetch_photo(candidates, url or "")
        except HTTPException:
            if not url:
                raise HTTPException(409, "Aucune photo dans la capture. Colle l'adresse d'une image de l'annonce.")
            page_url, html = fetch_html(url)
            found = photos.page_images(html, page_url)
            if not found:
                raise HTTPException(409, "Aucune photo trouvée. Colle l'adresse d'une image de l'annonce.")
            data, source = photos.fetch_photo(found, page_url)
    return {"photo": save_photo(ident, data, source)}


@app.patch("/api/listings/{ident}", dependencies=[Depends(authenticate)])
def edit_listing(ident: str, edit: ListingEdit):
    with Session.begin() as db:
        row = require_listing(db, ident, lock=True)
        if edit.revision is not None and edit.revision != row.revision:
            raise HTTPException(409, "Cette fiche a changé. Recharge-la avant d'enregistrer.")
        unknown = set(edit.facts) - Facts.model_fields.keys()
        if unknown or any(field not in Facts.model_fields for field in edit.unlock_fields):
            raise HTTPException(422, "Champ d'annonce inconnu.")
        overrides = dict(row.overrides)
        for field in edit.unlock_fields:
            overrides.pop(field, None)
        overrides.update(edit.facts)
        row.facts = Facts.model_validate({**row.report.get("facts", row.facts), **{k: v for k, v in overrides.items() if k in Facts.model_fields}}).model_dump()
        if edit.title is not None:
            row.title = edit.title
            overrides["title"] = edit.title
        row.overrides = overrides
        for field in ("notes", "status", "favorite", "reviewed"):
            if getattr(edit, field) is not None:
                setattr(row, field, getattr(edit, field))
        row.updated_at, row.revision = now(), row.revision + 1
        db.flush()
        return listing_json(row)


@app.delete("/api/listings/{ident}", dependencies=[Depends(authenticate)])
def delete_listing(ident: str):
    with Session.begin() as db:
        row = require_listing(db, ident, lock=True)
        db.execute(delete(CaptureRecord).where(CaptureRecord.listing_id == ident))
        db.execute(delete(Photo).where(Photo.listing_id == ident))
        for scenario in db.scalars(select(Scenario).where(Scenario.listing_id == ident)):
            scenario.listing_id = None
        db.delete(row)
    return {"ok": True}


@app.post("/api/import", dependencies=[Depends(authenticate)])
def import_listing(req: ImportRequest):
    if req.fetch_url:
        if not req.capture.url:
            raise HTTPException(400, "URL manquante")
        req.capture.url, req.capture.html = fetch_html(req.capture.url)
    return store_capture(req.capture)


@app.post("/capture", dependencies=[Depends(authenticate)])
def extension_capture(capture: Capture):
    row = store_capture(capture)
    return {"ok": True, "url": row["url"], "listing_id": row["id"], "listing": row,
            "message": "Annonce ajoutée à Repère. Vérifie sa fiche."}


@app.post("/api/preview", dependencies=[Depends(authenticate)])
def preview(capture: Capture):
    if capture.url:
        capture.url = public_url(capture.url)
    prepare_site_data(capture)
    return pipeline(capture)


@app.post("/api/listings/{ident}/reanalyze", dependencies=[Depends(authenticate)])
def reanalyze(ident: str, options: Capture):
    with Session() as db:
        require_listing(db, ident)
        capture = db.scalar(select(CaptureRecord).where(CaptureRecord.listing_id == ident).order_by(CaptureRecord.created_at.desc()))
        if not capture:
            raise HTTPException(409, "Pas de capture à réanalyser ; importe du texte ou du HTML.")
        payload = Capture.model_validate(capture.payload)
    payload.use_gemini, payload.parsers = options.use_gemini, options.parsers
    return store_capture(payload, ident)


def capture_hints(listing_id):
    """Indices de localisation de la dernière capture (Leboncoin : point, précision, publication, étages)."""
    with Session() as db:
        latest = db.scalar(select(CaptureRecord).where(CaptureRecord.listing_id == listing_id).order_by(CaptureRecord.created_at.desc()))
        if not latest:
            return {}
        payload = latest.payload or {}
    return sites.extract(payload.get("site_data"), payload.get("url") or "")["hints"]


def criteria_from(facts: dict, title: str, hints: dict, listing_id: str | None = None):
    """Critères de recherche d'adresse à partir d'une fiche ; None si elle ne suffit pas."""
    year = re.fullmatch(r"\s*(\d{4})\s*", str(facts.get("delivery") or ""))
    kind = hints.get("kind") or ("maison" if re.search(r"\b(?:maison|villa)\b", title or "", re.I) else
                                 "appartement" if re.search(r"\b(?:appartement|appt|studio|duplex|loft)\b", title or "", re.I) else "")
    try:
        return AddressSearch(listing_id=listing_id, postal_code=facts.get("postal_code"), city=facts.get("city"),
                             dpe_kwh_m2=facts.get("dpe_kwh_m2") or None, ges_kg_m2=facts.get("ges_kg_m2"),
                             area_m2=facts.get("area_m2") or None, dpe=facts.get("dpe") or "NC", ges=facts.get("ges") or "NC",
                             floor=facts.get("floor"), kind=kind,
                             building_year=int(year[1]) if year and 1700 <= int(year[1]) <= 2100 else None,
                             floors_building=hints.get("floors_building"), lat=hints.get("lat"), lng=hints.get("lng"),
                             geo_precise=bool(hints.get("geo_precise")), published=hints.get("published"))
    except ValidationError:
        return None


def auto_address_search(listing_id):
    """Après une capture : recherche d'adresse bornée à quelques secondes, rangée dans le rapport de la fiche.
    Désactivable avec ADEME_AUTO_SEARCH=0. N'écrit jamais le champ « Adresse retenue »."""
    if os.getenv("ADEME_AUTO_SEARCH", "1") == "0":
        return None
    with Session() as db:
        row = db.get(Listing, listing_id)
        if not row or (row.facts or {}).get("address"):
            return None
        facts, title = dict(row.facts or {}), row.title
    criteria = criteria_from(facts, title, capture_hints(listing_id), listing_id)
    if criteria is None:
        return None
    try:
        result = search_addresses(criteria, deadline=float(os.getenv("ADEME_AUTO_SEARCH_SECONDS", "8")))
    except HTTPException as exc:
        result = {"groups": [], "candidates": [], "confidence": "aucune", "warnings": [str(exc.detail)], "error": True}
    result["automatic"] = True
    with Session.begin() as db:
        row = db.get(Listing, listing_id)
        if row:
            row.report = {**(row.report or {}), "address_search": result}
    return result


@app.post("/api/dpe/address", dependencies=[Depends(authenticate)])
def dpe_address(criteria: AddressSearch):
    """Adresses probables d'après la base DPE publique de l'ADEME. Les valeurs du formulaire priment ; les indices
    de la dernière capture (point de l'annonce, date de publication…) complètent le classement."""
    if criteria.listing_id:
        hints = capture_hints(criteria.listing_id)
        updates = {k: v for k, v in hints.items() if k in AddressSearch.model_fields and getattr(criteria, k) in (None, "", False)}
        criteria = criteria.model_copy(update=updates)
    result = search_addresses(criteria)
    if criteria.listing_id:
        with Session.begin() as db:
            row = db.get(Listing, criteria.listing_id)
            if row:
                row.report = {**(row.report or {}), "address_search": result}
    return result


@app.get("/api/listings/{ident}/captures", dependencies=[Depends(authenticate)])
def captures(ident: str):
    with Session() as db:
        require_listing(db, ident)
        return [{"id": row.id, "created_at": row.created_at.isoformat(), "report": row.report}
                for row in db.scalars(select(CaptureRecord).where(CaptureRecord.listing_id == ident).order_by(CaptureRecord.created_at.desc()))]


@app.get("/api/listings/{ident}/source", dependencies=[Depends(authenticate)])
def source(ident: str):
    with Session() as db:
        require_listing(db, ident)
        row = db.scalar(select(CaptureRecord).where(CaptureRecord.listing_id == ident).order_by(CaptureRecord.created_at.desc()))
        if not row:
            raise HTTPException(404, "Aucune capture enregistrée")
        return {"capture": row.payload, "report": row.report}


@app.get("/api/profile", dependencies=[Depends(authenticate)])
def profile():
    with Session() as db:
        row = db.get(Setting, "profile")
        return row.value if row else Profile().model_dump()


@app.put("/api/profile", dependencies=[Depends(authenticate)])
def update_profile(profile: Profile):
    with Session.begin() as db:
        db.merge(Setting(key="profile", value=profile.model_dump()))
    return profile


@app.post("/api/simulate", dependencies=[Depends(authenticate)])
def simulate(inputs: Simulation):
    try:
        return calculate(inputs)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/api/scenarios", dependencies=[Depends(authenticate)])
def scenarios():
    with Session() as db:
        return [{"id": row.id, "name": row.name, "listing_id": row.listing_id, "inputs": row.inputs,
                 "result": row.result, "created_at": row.created_at.isoformat()}
                for row in db.scalars(select(Scenario).order_by(Scenario.created_at.desc()))]


@app.post("/api/scenarios", dependencies=[Depends(authenticate)])
def save_scenario(req: ScenarioSave):
    result = simulate(req.inputs)
    with Session.begin() as db:
        if req.listing_id:
            require_listing(db, req.listing_id)
        row = Scenario(name=req.name, listing_id=req.listing_id, inputs=req.inputs.model_dump(), result=result)
        db.add(row)
        db.flush()
        return {"id": row.id, "name": row.name}


@app.delete("/api/scenarios/{ident}", dependencies=[Depends(authenticate)])
def delete_scenario(ident: str):
    with Session.begin() as db:
        db.execute(delete(Scenario).where(Scenario.id == ident))
    return {"ok": True}


@app.get("/api/backup", dependencies=[Depends(authenticate)])
def backup():
    with Session() as db:
        return {"format": "repere.backup.v1", "exported_at": now().isoformat(), "profile": profile(),
                "listings": listings(), "scenarios": scenarios(),
                "captures": [{"id": row.id, "listing_id": row.listing_id, "payload": row.payload,
                              "report": row.report, "created_at": row.created_at.isoformat()} for row in db.scalars(select(CaptureRecord))]}


@app.post("/api/restore", dependencies=[Depends(authenticate)])
def restore(payload: dict):
    if payload.get("format") != "repere.backup.v1":
        raise HTTPException(422, "Sauvegarde Repère v1 attendue.")
    added, skipped = 0, 0
    try:
        with Session.begin() as db:
            for item in payload.get("listings", []):
                UUID(item["id"])
                if db.get(Listing, item["id"]) or (item.get("url") and db.scalar(select(Listing).where(Listing.url == item["url"]))):
                    skipped += 1
                    continue
                facts = Facts.model_validate(item["facts"]).model_dump()
                edit = ListingEdit(title=item["title"], notes=item.get("notes", ""), status=item.get("status", "À étudier"), facts=facts)
                restored_url = public_url(item["url"]) if item.get("url") else None
                db.add(Listing(id=item["id"], url=restored_url, url_hash=hashlib.sha256(restored_url.encode()).hexdigest() if restored_url else None, title=edit.title,
                    facts=facts, overrides={k: v for k, v in item.get("overrides", {}).items() if k in Facts.model_fields or k == "title"},
                    report=item.get("report", {}), notes=edit.notes, status=edit.status,
                    favorite=bool(item.get("favorite")), reviewed=bool(item.get("reviewed")),
                    created_at=datetime.fromisoformat(item["created_at"]), updated_at=datetime.fromisoformat(item["updated_at"])))
                db.flush()
                added += 1
            for item in payload.get("captures", []):
                UUID(item["id"])
                if db.get(CaptureRecord, item["id"]) or not db.get(Listing, item["listing_id"]):
                    continue
                capture = Capture.model_validate(item["payload"])
                capture.html = clean_html(capture.html) if capture.html else ""
                db.add(CaptureRecord(id=item["id"], listing_id=item["listing_id"], payload=capture.model_dump(),
                                     report=item.get("report", {}), created_at=datetime.fromisoformat(item["created_at"])))
            for item in payload.get("scenarios", []):
                UUID(item["id"])
                if db.get(Scenario, item["id"]):
                    continue
                inp = Simulation.model_validate(item["inputs"])
                req = ScenarioSave(name=item["name"], inputs=inp)
                db.add(Scenario(id=item["id"], name=req.name,
                    listing_id=item.get("listing_id") if db.get(Listing, item.get("listing_id", "")) else None,
                    inputs=inp.model_dump(), result=calculate(inp), created_at=datetime.fromisoformat(item["created_at"])))
            if not db.get(Setting, "profile"):
                db.add(Setting(key="profile", value=Profile.model_validate(payload.get("profile", {})).model_dump()))
    except (ValueError, KeyError, TypeError, IntegrityError) as exc:
        raise HTTPException(422, "Sauvegarde invalide ; aucune modification n'a été appliquée.") from exc
    return {"added": added, "skipped": skipped}


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
