"""Échéanciers explicites, indépendants de toute extraction et de toute IA.

Taux nominal annuel / 12, assurance sur capital initial. Les prêts complémentaires
peuvent différer le capital ; leurs intérêts et assurances restent payés pendant
le différé. Aucun lissage, éligibilité ou intérêt capitalisé n'est implicite.
"""
from __future__ import annotations
import math
from .schemas import Simulation

ENGINE_VERSION = "1.0"


def payment(principal: float, annual_rate: float, months: int) -> float:
    if principal <= 0:
        return 0.0
    rate = annual_rate / 1200
    return principal / months if rate == 0 else principal * rate / -math.expm1(-months * math.log1p(rate))


def loan_schedule(amount, annual_rate, months, insurance_pct=0, deferral=0):
    if amount <= 0:
        return []
    rate, balance = annual_rate / 1200, amount
    due, insurance = payment(amount, annual_rate, months), amount * insurance_pct / 1200
    rows = []
    for month in range(1, months + deferral + 1):
        interest = balance * rate
        principal = 0 if month <= deferral else min(balance, max(0, due - interest))
        if month == months + deferral:
            principal = balance
        balance = max(0, balance - principal)
        rows.append({"month": month, "principal": principal, "interest": interest,
                     "insurance": insurance, "payment": principal + interest + insurance, "balance": balance})
    return rows


def project_cost(s: Simulation):
    fees = s.acquisition_fees_eur if s.acquisition_fees_eur is not None else s.price_eur * s.acquisition_fees_pct / 100
    return s.price_eur + fees + s.works_eur + s.parking_extra + s.other_fees_eur, fees


def _schedule(s: Simulation, down_payment: float):
    total, _ = project_cost(s)
    additional = sum(loan.amount for loan in s.additional_loans)
    principal = total - down_payment - additional
    if principal < -0.011:
        raise ValueError("L'apport et les prêts complémentaires dépassent le coût du projet.")
    principal = max(0, principal)
    loans = [("Prêt principal", loan_schedule(principal, s.annual_rate_pct, s.loan_years * 12, s.insurance_pct))]
    loans += [(loan.name, loan_schedule(loan.amount, loan.annual_rate_pct, loan.repayment_months,
                                        loan.insurance_pct, loan.deferral_months)) for loan in s.additional_loans]
    rows = []
    for i in range(max((len(rows) for _, rows in loans), default=0)):
        row = {field: sum(rows[i][field] for _, rows in loans if i < len(rows))
               for field in ("principal", "interest", "insurance", "payment", "balance")}
        row["month"] = i + 1
        rows.append(row)
    return principal, rows, loans


def calculate(s: Simulation):
    total, fees = project_cost(s)
    down_payment = s.down_payment
    if sum(loan.amount for loan in s.additional_loans) > total + 0.001:
        raise ValueError("Les prêts complémentaires dépassent le coût du projet.")
    if s.mode == "contribution":
        if s.target_monthly is None:
            raise ValueError("Indique une mensualité maximale, assurance comprise.")
        high = total - sum(loan.amount for loan in s.additional_loans)
        def peak(amount):
            return max((r["payment"] for r in _schedule(s, amount)[1]), default=0)
        if peak(high) > s.target_monthly + 0.00001:
            raise ValueError("Les seuls prêts complémentaires dépassent la mensualité cible ; augmente la cible ou modifie ces prêts.")
        low = 0.0
        if peak(0) <= s.target_monthly:
            down_payment = 0.0
        else:
            for _ in range(50):
                mid = (low + high) / 2
                if peak(mid) > s.target_monthly:
                    low = mid
                else:
                    high = mid
            down_payment = min(total - sum(loan.amount for loan in s.additional_loans), math.ceil(high * 100) / 100)
    principal, rows, loans = _schedule(s, down_payment)
    maximum = max((row["payment"] for row in rows), default=0)
    running = sum((value or 0) / 12 for value in (s.charges_annual, s.tax_annual, s.maintenance_annual))
    missing = [label for key, label in {"charges_annual": "charges de copropriété", "tax_annual": "taxe foncière",
                                       "maintenance_annual": "entretien"}.items() if getattr(s, key) is None]
    phases = []
    for row in rows:
        if not phases or abs(phases[-1]["monthly"] - row["payment"]) > 0.02:
            phases.append({"start_month": row["month"], "end_month": row["month"], "monthly": row["payment"]})
        else:
            phases[-1]["end_month"] = row["month"]
    effort = (maximum + s.existing_debts_monthly) / s.income_monthly * 100 if s.income_monthly else None
    warnings = ["Taux, frais et assurance sont des hypothèses à confirmer. Assurance calculée sur le capital initial.",
                "Aucun lissage des prêts ni calcul automatique d'éligibilité aux aides."]
    if missing:
        warnings.append("Budget mensuel partiel : " + ", ".join(missing) + " non renseigné(s). Énergie et dépenses de vie non incluses.")
    if effort is not None and effort > s.effort_limit_pct:
        warnings.append("Le taux d'effort maximal dépasse le seuil renseigné ; cette simulation n'est pas une décision bancaire.")
    if s.savings_eur is not None and down_payment + s.reserve_eur > s.savings_eur:
        warnings.append("L'épargne renseignée ne couvre pas l'apport et la réserve souhaitée.")
    return {
        "engine_version": ENGINE_VERSION, "project_cost": round(total, 2), "acquisition_fees": round(fees, 2),
        "down_payment": round(down_payment, 2), "main_principal": round(principal, 2),
        "additional_principal": round(sum(loan.amount for loan in s.additional_loans), 2),
        "first_monthly": round(rows[0]["payment"], 2) if rows else 0,
        "peak_monthly": round(maximum, 2), "known_housing_monthly": round(maximum + running, 2),
        "recurring_monthly": round(running, 2), "missing_costs": missing,
        "total_interest": round(sum(r["interest"] for r in rows), 2),
        "total_insurance": round(sum(r["insurance"] for r in rows), 2),
        "effort_pct": round(effort, 2) if effort is not None else None,
        "cash_remaining": round(s.savings_eur - down_payment, 2) if s.savings_eur is not None else None,
        "cash_shortfall": round(max(0, down_payment + s.reserve_eur - s.savings_eur), 2) if s.savings_eur is not None else None,
        "monthly_remaining_known": round(s.income_monthly - maximum - running - s.existing_debts_monthly, 2) if s.income_monthly else None,
        "phases": [{**p, "monthly": round(p["monthly"], 2)} for p in phases],
        "schedule": [{k: round(v, 2) if k != "month" else v for k, v in r.items()} for r in rows],
        "loans": [{"name": name, "months": len(schedule), "peak_monthly": round(max((r["payment"] for r in schedule), default=0), 2)}
                  for name, schedule in loans], "warnings": warnings,
    }
