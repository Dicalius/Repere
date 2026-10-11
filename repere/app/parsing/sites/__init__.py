"""Adaptateurs propres à un site. Chacun lit les données structurées que le site publie lui-même et
renvoie des candidats de rang « site », le plus fiable du pipeline."""
from __future__ import annotations

from . import leboncoin

ADAPTERS = (leboncoin,)


def site_data_from_html(html: str, url: str) -> dict | None:
    for adapter in ADAPTERS:
        if adapter.matches(url):
            return adapter.from_html(html, url)
    return None


def extract(site_data: dict | None, url: str) -> dict:
    empty = {"candidates": [], "warnings": [], "title": "", "body": "", "images": [], "used": False, "parser": "", "hints": {}}
    if not isinstance(site_data, dict):
        return empty
    for adapter in ADAPTERS:
        if site_data.get("source") == adapter.SOURCE:
            return {**adapter.extract(site_data, url), "parser": adapter.PARSER}
    return empty
