from __future__ import annotations

from typing import Annotated, Any, Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Facts(Strict):
    # Projet d'achat ou de location. Les fiches antérieures (sans ce champ) sont des achats.
    transaction: Literal["Achat", "Location"] = "Achat"
    price_eur: float | None = Field(None, ge=0, le=100_000_000)
    # Location : loyer mensuel tel qu'affiché, charges locatives mensuelles, et si le loyer les inclut.
    rent_monthly: float | None = Field(None, ge=0, le=100_000)
    rent_charges_monthly: float | None = Field(None, ge=0, le=10_000)
    rent_charges_included: Literal["Oui", "Non", "Inconnu"] = "Inconnu"
    furnished: Literal["Meublé", "Non meublé", "Inconnu"] = "Inconnu"
    # ge=1 et non gt=0 : le SDK Gemini refuse « exclusiveMinimum » dans le schéma de sortie.
    area_m2: float | None = Field(None, ge=1, le=100000)
    city: str | None = Field(None, max_length=160)
    postal_code: str | None = Field(None, min_length=5, max_length=5)
    district: str | None = Field(None, max_length=200)
    # Adresse choisie par l'utilisateur (recherche DPE ADEME) ; jamais remplie automatiquement.
    address: str | None = Field(None, max_length=300)
    property_type: Literal["Neuf", "Ancien", "Ancien récent", "Inconnu"] = "Inconnu"
    rooms: str | None = Field(None, max_length=40)
    dpe: Literal["A", "B", "C", "D", "E", "F", "G", "NC"] = "NC"
    ges: Literal["A", "B", "C", "D", "E", "F", "G", "NC"] = "NC"
    dpe_kwh_m2: float | None = Field(None, ge=0, le=2000)
    ges_kg_m2: float | None = Field(None, ge=0, le=500)
    floor: str | None = Field(None, max_length=100)
    charges_annual: float | None = Field(None, ge=0, le=1_000_000)
    tax_annual: float | None = Field(None, ge=0, le=1_000_000)
    parking_extra: float | None = Field(None, ge=0, le=1_000_000)
    works_eur: float | None = Field(None, ge=0, le=10_000_000)
    station_minutes: float | None = Field(None, ge=0, le=500)
    delivery: str | None = Field(None, max_length=100)

    @field_validator("postal_code")
    @classmethod
    def french_postal_code(cls, value):
        if value is not None and not (value.isdigit() and "01000" <= value <= "98999"):
            raise ValueError("Code postal à 5 chiffres attendu")
        return value


class AddressSearch(Strict):
    """Critères envoyés à la base DPE de l'ADEME : lieu, valeurs ou lettres du DPE, surface.
    Les indices facultatifs (coordonnées, année, étages, date de publication) servent au classement local."""
    listing_id: str | None = Field(None, max_length=36)
    postal_code: str | None = Field(None, min_length=5, max_length=5, pattern=r"^\d{5}$")
    city: str | None = Field(None, max_length=160)
    dpe_kwh_m2: float | None = Field(None, gt=0, le=2000)
    ges_kg_m2: float | None = Field(None, ge=0, le=500)
    area_m2: float | None = Field(None, gt=0, le=100000)
    dpe: Literal["A", "B", "C", "D", "E", "F", "G", "NC"] = "NC"
    ges: Literal["A", "B", "C", "D", "E", "F", "G", "NC"] = "NC"
    floor: str | None = Field(None, max_length=100)
    kind: Literal["appartement", "maison", ""] = ""
    building_year: int | None = Field(None, ge=1700, le=2100)
    floors_building: int | None = Field(None, ge=0, le=200)
    lat: float | None = Field(None, ge=-90, le=90)
    lng: float | None = Field(None, ge=-180, le=180)
    geo_precise: bool = False
    published: str | None = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$")

    @model_validator(mode="after")
    def enough(self):
        if not self.postal_code and not (self.city or "").strip() and (self.lat is None or self.lng is None):
            raise ValueError("Indique le code postal ou la ville du bien.")
        if self.dpe_kwh_m2 is None and not (self.area_m2 and (self.dpe != "NC" or self.ges != "NC")):
            raise ValueError("Il faut la consommation du DPE (kWh/m²/an), ou la surface avec la lettre DPE ou GES.")
        return self


class Capture(Strict):
    url: str = Field(default="", max_length=4096)
    title: str = Field(default="", max_length=1000)
    text: str = Field(default="", max_length=120000)
    html: str = Field(default="", max_length=1_500_000)
    metadata: dict[str, str] = Field(default_factory=dict, max_length=30)
    json_ld: list[str] = Field(default_factory=list, max_length=30)
    selection_only: bool = False
    use_gemini: bool = False
    capture_warnings: list[str] = Field(default_factory=list, max_length=20)
    # Grandes images du contenu principal repérées par l'extension (photo de la fiche)
    images: list[Annotated[str, Field(max_length=2048)]] = Field(default_factory=list, max_length=5)
    # Données structurées propres à un site (ex. Leboncoin : props.pageProps.ad réduit par l'extension)
    site_data: dict[str, Any] | None = None
    parsers: list[Literal["structured", "html", "text", "trafilatura"]] = Field(
        default_factory=lambda: ["structured", "html", "text", "trafilatura"], max_length=4)

    @model_validator(mode="after")
    def reasonable_size(self):
        if sum(len(k) + len(v) for k, v in self.metadata.items()) > 30000:
            raise ValueError("Métadonnées trop volumineuses")
        if sum(map(len, self.json_ld)) > 300000:
            raise ValueError("JSON-LD trop volumineux")
        if self.site_data is not None:
            import json
            if len(json.dumps(self.site_data, ensure_ascii=False)) > 300000:
                raise ValueError("Données du site trop volumineuses")
        return self


class PhotoRequest(Strict):
    url: str | None = Field(None, max_length=2048)


class ImportRequest(Strict):
    capture: Capture
    fetch_url: bool = False


class ListingEdit(Strict):
    revision: int | None = None
    title: str | None = Field(None, max_length=1000)
    facts: dict[str, Any] = Field(default_factory=dict)
    unlock_fields: list[str] = Field(default_factory=list, max_length=30)
    notes: str | None = Field(None, max_length=10000)
    status: Literal["À étudier", "À visiter", "Visité", "Offre", "Écarté"] | None = None
    favorite: bool | None = None
    reviewed: bool | None = None


class Profile(Strict):
    income_monthly: float | None = Field(None, gt=0, le=1_000_000)
    savings_eur: float | None = Field(None, ge=0, le=100_000_000)
    existing_debts_monthly: float = Field(0, ge=0, le=1_000_000)
    reserve_eur: float = Field(0, ge=0, le=100_000_000)
    target_monthly: float | None = Field(None, gt=0, le=1_000_000)
    effort_limit_pct: float = Field(35, gt=0, le=100)
    loan_years: int = Field(25, ge=1, le=40)
    annual_rate_pct: float = Field(3.5, ge=0, le=30)
    insurance_pct: float = Field(0.3, ge=0, le=10)
    fees_old_pct: float = Field(8, ge=0, le=25)
    fees_new_pct: float = Field(3, ge=0, le=25)


class AdditionalLoan(Strict):
    name: str = Field("Prêt complémentaire", min_length=1, max_length=100)
    amount: float = Field(gt=0, le=100_000_000)
    annual_rate_pct: float = Field(0, ge=0, le=30)
    repayment_months: int = Field(180, ge=1, le=480)
    deferral_months: int = Field(0, ge=0, le=240)
    insurance_pct: float = Field(0, ge=0, le=10)


class Simulation(Strict):
    price_eur: float = Field(gt=0, le=100_000_000)
    acquisition_fees_pct: float = Field(8, ge=0, le=25)
    acquisition_fees_eur: float | None = Field(None, ge=0, le=25_000_000)
    works_eur: float = Field(0, ge=0, le=10_000_000)
    parking_extra: float = Field(0, ge=0, le=1_000_000)
    other_fees_eur: float = Field(0, ge=0, le=1_000_000)
    down_payment: float = Field(0, ge=0, le=100_000_000)
    loan_years: int = Field(25, ge=1, le=40)
    annual_rate_pct: float = Field(3.5, ge=0, le=30)
    insurance_pct: float = Field(0.3, ge=0, le=10)
    additional_loans: list[AdditionalLoan] = Field(default_factory=list, max_length=5)
    charges_annual: float | None = Field(None, ge=0, le=1_000_000)
    tax_annual: float | None = Field(None, ge=0, le=1_000_000)
    maintenance_annual: float | None = Field(None, ge=0, le=1_000_000)
    income_monthly: float | None = Field(None, gt=0, le=1_000_000)
    existing_debts_monthly: float = Field(0, ge=0, le=1_000_000)
    savings_eur: float | None = Field(None, ge=0, le=100_000_000)
    reserve_eur: float = Field(0, ge=0, le=100_000_000)
    effort_limit_pct: float = Field(35, gt=0, le=100)
    mode: Literal["payment", "contribution"] = "payment"
    target_monthly: float | None = Field(None, gt=0, le=1_000_000)


class ScenarioSave(Strict):
    name: str = Field(min_length=1, max_length=150)
    listing_id: str | None = None
    inputs: Simulation
