import math
import pytest
from app.finance import calculate, loan_schedule, payment
from app.schemas import Simulation, AdditionalLoan


def test_zero_rate_cash_flow_and_principal_conservation():
    rows = loan_schedule(180000, 0, 240, 0)
    assert all(row["payment"] == 750 for row in rows)
    assert sum(row["principal"] for row in rows) == 180000
    assert rows[-1]["balance"] == 0


def test_annuity_reference_and_total_cash_flow():
    # 100 000 €, 12 % nominal / an, 12 mois. Référence calculée par la somme
    # des facteurs d'actualisation, indépendante de la formule d'implémentation.
    expected = 100000 / sum((1.01) ** -month for month in range(1, 13))
    assert payment(100000, 12, 12) == pytest.approx(expected, abs=0.00001)
    rows = loan_schedule(100000, 12, 12, 0.3)
    assert sum(r["principal"] for r in rows) == pytest.approx(100000, abs=0.00001)
    assert all(r["insurance"] == 25 for r in rows)
    assert rows[-1]["balance"] == 0


def test_deferred_auxiliary_loan_exposes_later_peak():
    s = Simulation(price_eur=200000, acquisition_fees_pct=0, down_payment=20000,
                   loan_years=20, annual_rate_pct=0, insurance_pct=0,
                   additional_loans=[AdditionalLoan(name="PTZ confirmé", amount=40000, repayment_months=120, deferral_months=120)])
    out = calculate(s)
    assert out["first_monthly"] == pytest.approx(140000 / 240, abs=0.01)
    assert out["peak_monthly"] == pytest.approx(140000 / 240 + 40000 / 120, abs=0.01)
    assert len(out["phases"]) == 2
    assert out["schedule"][-1]["balance"] == 0


def test_deferral_pays_interest_without_capitalization():
    rows = loan_schedule(12000, 12, 12, 1, 6)
    assert rows[0]["principal"] == 0
    assert rows[0]["interest"] == 120
    assert rows[0]["insurance"] == 10
    assert rows[5]["balance"] == 12000
    assert rows[-1]["balance"] == 0


def test_reverse_apport_meets_all_months_including_deferred_period():
    s = Simulation(price_eur=250000, acquisition_fees_pct=8, mode="contribution", target_monthly=950,
                   additional_loans=[AdditionalLoan(name="Prêt", amount=30000, repayment_months=120, deferral_months=120)])
    out = calculate(s)
    assert max(r["payment"] for r in out["schedule"]) <= 950.01
    assert out["down_payment"] > 0
    fewer = s.model_copy(update={"mode": "payment", "down_payment": out["down_payment"] - 10})
    assert calculate(fewer)["peak_monthly"] > 950


def test_reverse_known_exact_zero_interest_case():
    out = calculate(Simulation(price_eur=200000, acquisition_fees_pct=0, loan_years=20,
                    annual_rate_pct=0, insurance_pct=0, mode="contribution", target_monthly=500))
    assert out["down_payment"] == pytest.approx(80000, abs=0.01)
    assert out["peak_monthly"] == 500


def test_reverse_impossible_auxiliary_target():
    with pytest.raises(ValueError, match="complémentaires"):
        calculate(Simulation(price_eur=100000, mode="contribution", target_monthly=100,
                  additional_loans=[AdditionalLoan(amount=60000, repayment_months=120)]))


def test_unknown_costs_are_distinguished_from_zero():
    s = Simulation(price_eur=150000, charges_annual=0, tax_annual=None, maintenance_annual=0)
    out = calculate(s)
    assert out["missing_costs"] == ["taxe foncière"]
    assert out["effort_pct"] is None
    assert out["monthly_remaining_known"] is None


def test_exact_fees_override_percentage_and_cash_purchase():
    out = calculate(Simulation(price_eur=100000, acquisition_fees_pct=8, acquisition_fees_eur=5000,
                    down_payment=105000, charges_annual=1200, tax_annual=600, maintenance_annual=0))
    assert out["acquisition_fees"] == 5000
    assert out["peak_monthly"] == 0
    assert out["schedule"] == []
    assert out["known_housing_monthly"] == 150


def test_effort_includes_insurance_and_existing_debt():
    out = calculate(Simulation(price_eur=120000, acquisition_fees_pct=0, annual_rate_pct=0,
                    insurance_pct=1, loan_years=10, income_monthly=4000, existing_debts_monthly=200))
    assert out["peak_monthly"] == 1100
    assert out["effort_pct"] == 32.5


def test_invalid_values_rejected():
    with pytest.raises(ValueError):
        Simulation(price_eur=float("nan"))
    with pytest.raises(ValueError):
        calculate(Simulation(price_eur=100000, down_payment=200000))


def test_all_additional_loans_continue_after_main_loan():
    out = calculate(Simulation(price_eur=100000, acquisition_fees_pct=0, loan_years=1, annual_rate_pct=0, insurance_pct=0,
                    additional_loans=[AdditionalLoan(amount=24000, deferral_months=24, repayment_months=24)]))
    assert len(out["schedule"]) == 48
    assert out["schedule"][15]["payment"] == 0
    assert out["schedule"][24]["payment"] == 1000
