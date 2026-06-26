"""Discount note pricer for repos, commercial paper, and short-term interest-bearing instruments.

Covers any money-market instrument whose value is a single maturity cash flow discounted
back to the evaluation date using a risk-free curve plus a credit (z-)spread.  No schedule
or coupon structure is required.

Instrument types (instrument_type field)
-----------------------------------------
  discount          Zero-coupon / issued-at-a-discount. Maturity CF = face_value.
                    Used for: commercial paper (CP), T-bills, discount CDs.

  interest_bearing  Simple-interest note. Maturity CF = face_value × (1 + coupon_rate × t).
                    Used for: repos, term deposits, interest-at-maturity CDs, banker's
                    acceptances, and any instrument where interest accrues linearly from
                    issue to maturity at a fixed annualised rate.

Pricing formula
---------------
  NPV  = maturity_CF × DF(maturity) × exp(−credit_spread × t)
  DF() is read from the discount curve selected by currency / configuration.

Quoted yield metrics returned
------------------------------
  discount_rate   Bank discount convention: (FV − NPV) / FV × basis / t_days
                  (standard US CP and T-bill quoting convention, basis = 360)
  simple_yield    Money-market yield: (FV − NPV) / NPV × basis / t_days
  ytm             Continuously compounded yield extracted from the discount factor

Required JSON fields
--------------------
  instrument_id    ISIN or internal identifier
  evaluation_date  Pricing date (DD-MM-YYYY or YYYY-MM-DD)
  issue_date       Start date of the instrument (DD-MM-YYYY or YYYY-MM-DD)
  maturity_date    Maturity / repurchase date (DD-MM-YYYY or YYYY-MM-DD)
  face_value       Face / redemption amount (e.g. 1 000 000)

Optional JSON fields
--------------------
  instrument_type  discount (default) | interest_bearing
  coupon_rate      Annualised rate for interest_bearing type (decimal or %)
  day_count        Actual360 (default) | Actual365Fixed | ACT/ACT (ICMA)
  credit_spread_bp Z-spread over the discount curve in basis points (default 0)
  settlement_days  Days to settlement (default 0 — money-market same-day convention)
  calendar         TARGET (default) | UnitedStates
  description      Human-readable name
  repo_collateral  Dict with collateral details (informational — not used in pricing):
                     { instrument_id, description, market_value, haircut_pct }
"""

import argparse
import math
from pathlib import Path

import QuantLib as ql

try:
    from models.helper import (
        today_date_string, parse_date, get_calendar, get_day_count,
        normalize_rate, load_json,
        select_discount_curve_config, build_discount_curve,
    )
except (ModuleNotFoundError, ImportError):
    from helper import (
        today_date_string, parse_date, get_calendar, get_day_count,
        normalize_rate, load_json,
        select_discount_curve_config, build_discount_curve,
    )

try:
    from reporting import pdf_report
except (ModuleNotFoundError, ImportError):
    import reporting.pdf_report as pdf_report

BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent
CURVES_DIR = PROJECT_ROOT / 'curves'
CURVE_FILE = CURVES_DIR / 'swap_curves.json'

_MM_BASIS = {
    'Actual360':        360,
    'Actual365Fixed':   365,
    'ACT/ACT (ICMA)':   365,
}


# ---------------------------------------------------------------------------
# Core pricer
# ---------------------------------------------------------------------------

def price_asset(bond_data, curve_json, issuer_spread_bp=None):
    evaluation_date = parse_date(bond_data.get('evaluation_date', today_date_string()))
    ql.Settings.instance().evaluationDate = evaluation_date

    discount_curve_cfg = select_discount_curve_config(curve_json, bond_data)
    discount_curve = build_discount_curve(discount_curve_cfg, evaluation_date)
    discount_curve_name = discount_curve_cfg.get('curve_name')

    if issuer_spread_bp is None:
        issuer_spread_bp = float(bond_data.get('issuer_spread_bp',
                                                bond_data.get('credit_spread_bp', 0.0)))

    face_value    = float(bond_data.get('face_value', bond_data.get('par', 100.0)))
    settlement_days = int(bond_data.get('settlement_days', 0))
    calendar      = get_calendar(bond_data.get('calendar', 'TARGET'))
    day_count     = get_day_count(bond_data.get('day_count', 'Actual360'))
    instr_type    = str(bond_data.get('instrument_type', 'discount')).lower()

    issue_date    = parse_date(bond_data['issue_date'])
    maturity_date = parse_date(bond_data['maturity_date'])
    settlement_date = calendar.advance(evaluation_date, settlement_days, ql.Days)

    z_spread = issuer_spread_bp / 10_000.0

    # --- maturity cash flow ---
    if instr_type == 'interest_bearing':
        coupon_rate = normalize_rate(bond_data.get('coupon_rate', 0.0))
        t_total     = day_count.yearFraction(issue_date, maturity_date)
        maturity_cf = face_value * (1.0 + coupon_rate * t_total)
        t_accrued   = day_count.yearFraction(issue_date, settlement_date)
        accrued     = face_value * coupon_rate * t_accrued
    else:
        coupon_rate = 0.0
        maturity_cf = face_value
        accrued     = 0.0

    # --- discount factor and NPV ---
    t_mat  = ql.Actual365Fixed().yearFraction(evaluation_date, maturity_date)
    df_mat = discount_curve.discount(maturity_date) * math.exp(-z_spread * t_mat)
    npv    = maturity_cf * df_mat

    clean_price = npv - accrued

    # --- yield metrics ---
    # Continuously compounded yield
    ytm = -math.log(df_mat) / t_mat if t_mat > 0 else 0.0

    # Bank discount rate and simple yield (money-market conventions)
    basis     = float(_MM_BASIS.get(bond_data.get('day_count', 'Actual360'), 360))
    t_days    = day_count.dayCount(evaluation_date, maturity_date)
    discount_rate = (maturity_cf - npv) / maturity_cf * basis / t_days if t_days > 0 else None
    simple_yield  = (maturity_cf - npv) / npv * basis / t_days if (t_days > 0 and npv > 0) else None

    return {
        'selected_npv':          npv,
        'npv':                   npv,
        'npv_to_maturity':       npv,
        'npv_to_worst_call':     npv,
        'npv_to_first_call':     npv,
        'dirty_price':           npv,
        'clean_price':           clean_price,
        'accrued':               accrued,
        'face_value':            face_value,
        'maturity_cashflow':     maturity_cf,
        'coupon_rate':           coupon_rate,
        'ytm':                   ytm,
        'discount_rate':         discount_rate,
        'simple_yield':          simple_yield,
        'issuer_spread_bp':      issuer_spread_bp,
        'evaluation_date':       evaluation_date.ISO(),
        'issue_date':            issue_date.ISO(),
        'maturity_date':         maturity_date.ISO(),
        'settlement_date':       settlement_date.ISO(),
        'discount_curve_name':   discount_curve_name,
        'cashflows': [{
            'date':   maturity_date.ISO(),
            'type':   'redemption',
            'amount': maturity_cf,
            'df':     df_mat,
            'pv':     npv,
        }],
        'price_pct': {
            'pv_note':                npv / face_value * 100.0,
            'pv_note_to_maturity':    npv / face_value * 100.0,
            'pv_note_to_worst_call':  npv / face_value * 100.0,
            'clean_price':            clean_price / face_value * 100.0,
        },
    }


# ---------------------------------------------------------------------------
# CLI output
# ---------------------------------------------------------------------------

def print_result(bond_data, result):
    print(f"{bond_data.get('description', bond_data.get('instrument_id'))} "
          f"({bond_data.get('instrument_id')})")
    print(f"Instrument type:   {bond_data.get('instrument_type', 'discount')}")
    print(f"Evaluation date:   {result['evaluation_date']}")
    print(f"Settlement date:   {result['settlement_date']}")
    print(f"Maturity date:     {result['maturity_date']}")
    print(f"Discount curve:    {result.get('discount_curve_name', '-')}")
    print(f"Issuer spread:     {result['issuer_spread_bp']:.2f} bp")
    print(f"Face value:        {result['face_value']:,.4f}")
    print(f"Maturity CF:       {result['maturity_cashflow']:,.4f}")
    print(f"NPV (dirty):       {result['dirty_price']:,.6f}")
    print(f"Accrued:           {result['accrued']:,.6f}")
    print(f"NPV (clean):       {result['clean_price']:,.6f}")
    if result['discount_rate'] is not None:
        print(f"Discount rate:     {result['discount_rate'] * 100:.6f}%")
    if result['simple_yield'] is not None:
        print(f"Simple yield:      {result['simple_yield'] * 100:.6f}%")
    print(f"YTM (cont. comp.): {result['ytm'] * 100:.6f}%")
    print()


def parse_args():
    parser = argparse.ArgumentParser(
        description='Price discount notes, repos, and commercial paper.')
    parser.add_argument('--bond-file', required=True, help='Path to instrument JSON file')
    parser.add_argument('--curve-file', default=str(CURVE_FILE), help='Path to curve JSON file')
    parser.add_argument('--issuer-spread-bp', type=float, default=None)
    return parser.parse_args()


def main():
    args = parse_args()
    bond_data  = load_json(Path(args.bond_file))
    curve_json = load_json(Path(args.curve_file))
    result = price_asset(bond_data, curve_json, issuer_spread_bp=args.issuer_spread_bp)
    print_result(bond_data, result)
    pdf_path = pdf_report.create_pdf_report(
        model_name='discount_note',
        instrument_id=bond_data.get('instrument_id', 'unknown'),
        input_payload=bond_data,
        output_payload=result,
    )
    print(f'PDF report: {pdf_path}')


if __name__ == '__main__':
    main()
