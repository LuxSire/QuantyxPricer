"""Plain-vanilla inflation-linked bond pricer.

Covers government-style linkers such as US TIPS, UK Gilts, OATi/OAT€i,
BTPs-i and similar instruments where:

  - Coupons = real_coupon_rate × notional × IndexRatio(payment_date) × accrual
  - Redemption = notional × IndexRatio(maturity) [floored at par if redemption_floor=true]
  - IndexRatio(d) = CPI(d − lag) / base_cpi

Forward index ratios are projected from the current (known) index ratio using
a flat annual inflation assumption:

  IndexRatio(d) = current_index_ratio × (1 + annual_inflation_rate) ^ year_fraction(eval_date, d)

Required JSON fields
--------------------
  real_coupon_rate         Annual real coupon rate (decimal or %, auto-normalised)
  base_cpi                 CPI level at issuance (denominator of index ratio)
  current_cpi              CPI level at / near evaluation date
  annual_inflation_rate    Forward flat inflation assumption (decimal or %)
  issue_date               DD-MM-YYYY or YYYY-MM-DD
  maturity_date            DD-MM-YYYY or YYYY-MM-DD
  coupon_frequency         Annual | Semiannual | Quarterly | Monthly
  accrual_day_count        ACT/ACT (ICMA) typical for government linkers
  calendar                 TARGET | UnitedStates
  evaluation_date          Pricing date (DD-MM-YYYY or YYYY-MM-DD)

Optional JSON fields
--------------------
  business_day_convention  ModifiedFollowing (default)
  date_generation          Backward (default)
  par                      Face value (default 100)
  credit_spread_bp         Z-spread in basis points (default 0)
  settlement_days          Days to settlement (default 2)
  redemption_floor         Bool — floor inflation-accreted redemption at par (default true)
  nominal_yield            If provided, breakeven inflation is computed
  first_coupon_date        For bonds with a short/long first coupon period
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
ASSETS_DIR = PROJECT_ROOT / 'assets'
CURVES_DIR = PROJECT_ROOT / 'curves'
CURVE_FILE = CURVES_DIR / 'swap_curves.json'


# ---------------------------------------------------------------------------
# Schedule helpers
# ---------------------------------------------------------------------------

_FREQUENCIES = {
    'Annual': ql.Annual,
    'Semiannual': ql.Semiannual,
    'Quarterly': ql.Quarterly,
    'Monthly': ql.Monthly,
}

_BDC = {
    'ModifiedFollowing': ql.ModifiedFollowing,
    'Following': ql.Following,
    'Unadjusted': ql.Unadjusted,
}

_DATE_GEN = {
    'Backward': ql.DateGeneration.Backward,
    'Forward': ql.DateGeneration.Forward,
}


def _build_schedule(bond_data):
    issue_date = parse_date(bond_data['issue_date'])
    maturity_date = parse_date(bond_data.get('maturity_date') or bond_data['end_date'])
    calendar = get_calendar(bond_data.get('calendar', 'TARGET'))
    freq = _FREQUENCIES.get(bond_data.get('coupon_frequency', 'Semiannual'), ql.Semiannual)
    bdc = _BDC.get(bond_data.get('business_day_convention', 'ModifiedFollowing'), ql.ModifiedFollowing)
    gen = _DATE_GEN.get(bond_data.get('date_generation', 'Backward'), ql.DateGeneration.Backward)

    first_coupon = bond_data.get('first_coupon_date')
    _first_coupon_parsed = parse_date(first_coupon) if first_coupon else ql.Date()
    # Discard if on or before issue_date — QuantLib requires it to be strictly after
    first_coupon_date = _first_coupon_parsed if (first_coupon and _first_coupon_parsed > issue_date) else ql.Date()

    return ql.Schedule(
        issue_date,
        maturity_date,
        ql.Period(freq),
        calendar,
        bdc,
        bdc,
        gen,
        False,               # end-of-month
        first_coupon_date,
    ), maturity_date


# ---------------------------------------------------------------------------
# Index ratio projection
# ---------------------------------------------------------------------------

def _index_ratio(bond_data, target_date, eval_date, day_count):
    """Return IndexRatio at target_date projected from the current known CPI.

    IndexRatio(d) = current_index_ratio × (1 + annual_inflation_rate)^t
    where t = year_fraction(eval_date, target_date).

    If target_date <= eval_date the ratio is the current index ratio (no
    forward projection needed, we may be discounting past coupons which
    are already known — but those are filtered out before calling this).
    """
    if 'base_cpi' in bond_data and 'current_cpi' in bond_data:
        base_cpi = float(bond_data['base_cpi'])
        current_cpi = float(bond_data['current_cpi'])
        if base_cpi <= 0:
            raise ValueError('base_cpi must be positive')
        current_index_ratio = current_cpi / base_cpi
    else:
        # Fallback: index_linked_assumption or collateral.inflation_assumption style
        assump = (
            bond_data.get('index_linked_assumption') or
            bond_data.get('collateral', {}).get('inflation_assumption') or {}
        )
        current_index_ratio = float(assump.get('index_ratio_at_eval', bond_data.get('index_ratio_at_eval', 1.0)))

    # annual_inflation_rate: top-level wins, then index_linked_assumption
    assump_fallback = (
        bond_data.get('index_linked_assumption') or
        bond_data.get('collateral', {}).get('inflation_assumption') or {}
    )
    raw_rate = (
        bond_data.get('annual_inflation_rate') or
        assump_fallback.get('annual_index_growth_rate') or
        assump_fallback.get('annual_inflation_rate') or
        0.0
    )
    annual_inflation = normalize_rate(raw_rate)
    t = day_count.yearFraction(eval_date, target_date)
    if t <= 0:
        return current_index_ratio
    return current_index_ratio * ((1.0 + annual_inflation) ** t)


# ---------------------------------------------------------------------------
# Accrued coupon
# ---------------------------------------------------------------------------

def _accrued_coupon(bond_data, schedule, eval_date, settlement_date, day_count, real_coupon_rate, par):
    """Return the inflation-adjusted accrued coupon at settlement_date."""
    for i in range(1, len(schedule)):
        d0 = schedule[i - 1]
        d1 = schedule[i]
        if d0 <= settlement_date < d1:
            accrual = day_count.yearFraction(d0, settlement_date)
            # Accrue at the index ratio of the settlement date
            index_ratio = _index_ratio(bond_data, settlement_date, eval_date, day_count)
            return par * real_coupon_rate * accrual * index_ratio
    return 0.0


# ---------------------------------------------------------------------------
# Core pricer
# ---------------------------------------------------------------------------

def price_sensitivity(bond_data, curve_json, n_steps=2, step_pct=0.10):
    base = normalize_rate(bond_data.get('annual_inflation_rate', 0.0))
    if base == 0.0:
        return []
    multipliers = [1.0 + (i - n_steps) * step_pct for i in range(2 * n_steps + 1)]
    sensitivity = []
    for m in multipliers:
        level = round(base * m, 8)
        d = {**bond_data, 'annual_inflation_rate': level}
        r = price_asset(d, curve_json, _skip_sensitivity=True)
        sensitivity.append({'spread_bp': round(level * 100, 6), 'pv_note_pct': r['price_pct']['pv_note']})
    return sensitivity


def price_asset(bond_data, curve_json, issuer_spread_bp=None, _skip_sensitivity=False):
    evaluation_date = parse_date(bond_data.get('evaluation_date', today_date_string()))
    ql.Settings.instance().evaluationDate = evaluation_date

    discount_curve_cfg = select_discount_curve_config(curve_json, bond_data)
    discount_curve = build_discount_curve(discount_curve_cfg, evaluation_date)
    discount_curve_name = discount_curve_cfg.get('curve_name')

    if issuer_spread_bp is None:
        issuer_spread_bp = float(bond_data.get('issuer_spread_bp', bond_data.get('credit_spread_bp', 0.0)))

    par = float(bond_data.get('par', 100.0))
    # real_coupon_rate: top-level wins, then fixed_coupon_rate, then index_linked_assumption.coupon_multiplier
    _assump = (
        bond_data.get('index_linked_assumption') or
        bond_data.get('collateral', {}).get('inflation_assumption') or {}
    )
    real_coupon_rate = normalize_rate(
        bond_data.get('real_coupon_rate') or
        bond_data.get('fixed_coupon_rate') or
        _assump.get('coupon_multiplier') or
        0.0
    )
    redemption_floor = bool(bond_data.get('redemption_floor', True))
    settlement_days = int(bond_data.get('settlement_days', 2))

    day_count = get_day_count(bond_data.get('accrual_day_count', 'ACT/ACT (ICMA)'))
    calendar = get_calendar(bond_data.get('calendar', 'TARGET'))
    bdc = _BDC.get(bond_data.get('business_day_convention', 'ModifiedFollowing'), ql.ModifiedFollowing)
    settlement_date = calendar.advance(evaluation_date, settlement_days, ql.Days, bdc)

    schedule, maturity_date = _build_schedule(bond_data)

    z_spread = issuer_spread_bp / 10000.0

    # --- coupon PV ---
    pv_coupons = 0.0
    cashflows = []
    for i in range(1, len(schedule)):
        d0 = schedule[i - 1]
        d1 = schedule[i]
        if d1 <= evaluation_date:
            continue
        accrual = day_count.yearFraction(d0, d1)
        index_ratio = _index_ratio(bond_data, d1, evaluation_date, day_count)
        coupon_cf = par * real_coupon_rate * accrual * index_ratio
        t = ql.Actual365Fixed().yearFraction(evaluation_date, d1)
        df = discount_curve.discount(d1) * math.exp(-z_spread * t)
        pv = coupon_cf * df
        pv_coupons += pv
        cashflows.append({
            'date': d1.ISO(),
            'type': 'coupon',
            'index_ratio': index_ratio,
            'amount': coupon_cf,
            'df': df,
            'pv': pv,
        })

    # --- redemption PV ---
    index_ratio_maturity = _index_ratio(bond_data, maturity_date, evaluation_date, day_count)
    inflation_accreted_redemption = par * index_ratio_maturity
    if redemption_floor:
        inflation_accreted_redemption = max(inflation_accreted_redemption, par)
    t_mat = ql.Actual365Fixed().yearFraction(evaluation_date, maturity_date)
    df_mat = discount_curve.discount(maturity_date) * math.exp(-z_spread * t_mat)
    pv_redemption = inflation_accreted_redemption * df_mat
    cashflows.append({
        'date': maturity_date.ISO(),
        'type': 'redemption',
        'index_ratio': index_ratio_maturity,
        'amount': inflation_accreted_redemption,
        'df': df_mat,
        'pv': pv_redemption,
    })

    npv = pv_coupons + pv_redemption

    # --- accrued coupon and clean price ---
    accrued = _accrued_coupon(bond_data, schedule, evaluation_date, settlement_date, day_count, real_coupon_rate, par)
    clean_price = npv - accrued

    # --- inflation-accreted (real) values ---
    index_ratio_settlement = _index_ratio(bond_data, settlement_date, evaluation_date, day_count)
    inflation_accreted_principal = par * index_ratio_settlement

    # --- real yield (YTM in real terms) via bisection ---
    real_ytm = _solve_real_ytm(cashflows, evaluation_date, npv, day_count)

    # --- breakeven inflation vs nominal yield ---
    nominal_yield = bond_data.get('nominal_yield')
    breakeven_inflation = None
    if nominal_yield is not None and real_ytm is not None:
        r_nominal = normalize_rate(float(nominal_yield))
        breakeven_inflation = (1.0 + r_nominal) / (1.0 + real_ytm) - 1.0

    result = {
        'selected_npv': npv,
        'npv': npv,
        'npv_to_maturity': npv,
        'npv_to_worst_call': npv,
        'npv_to_first_call': npv,
        'clean_price': clean_price,
        'dirty_price': npv,
        'accrued_coupon': accrued,
        'inflation_accreted_principal': inflation_accreted_principal,
        'index_ratio_settlement': index_ratio_settlement,
        'index_ratio_maturity': index_ratio_maturity,
        'real_ytm': real_ytm,
        'breakeven_inflation': breakeven_inflation,
        'issuer_spread_bp': issuer_spread_bp,
        'evaluation_date': evaluation_date.ISO(),
        'maturity_date': maturity_date.ISO(),
        'settlement_date': settlement_date.ISO(),
        'discount_curve_name': discount_curve_name,
        'cashflows': cashflows,
        'price_pct': {
            'pv_note': npv / par * 100.0,
            'pv_note_to_maturity': npv / par * 100.0,
            'pv_note_to_worst_call': npv / par * 100.0,
            'clean_price': clean_price / par * 100.0,
        },
    }
    if not _skip_sensitivity:
        result['sensitivity'] = price_sensitivity(bond_data, curve_json)
    return result


# ---------------------------------------------------------------------------
# Real YTM solver (bisection on continuous compounding)
# ---------------------------------------------------------------------------

def _solve_real_ytm(cashflows, eval_date, npv, day_count, low=-0.5, high=1.0, tol=1e-9, max_iter=120):
    if npv <= 0 or not cashflows:
        return None

    def pv_at(y):
        total = 0.0
        for cf in cashflows:
            d_parts = cf['date'].split('-')
            cf_date = ql.Date(int(d_parts[2]), int(d_parts[1]), int(d_parts[0]))
            t = ql.Actual365Fixed().yearFraction(eval_date, cf_date)
            if t > 0:
                total += cf['amount'] * math.exp(-y * t)
        return total

    f_low = pv_at(low) - npv
    f_high = pv_at(high) - npv

    for _ in range(20):
        if f_low * f_high <= 0:
            break
        high += 0.5
        f_high = pv_at(high) - npv

    if f_low * f_high > 0:
        return None

    for _ in range(max_iter):
        mid = 0.5 * (low + high)
        f_mid = pv_at(mid) - npv
        if abs(f_mid) < tol:
            return mid
        if f_low * f_mid <= 0:
            high = mid
            f_high = f_mid
        else:
            low = mid
            f_low = f_mid
    return 0.5 * (low + high)


# ---------------------------------------------------------------------------
# CLI output
# ---------------------------------------------------------------------------

def print_result(bond_data, result):
    print(f"{bond_data.get('description', bond_data.get('instrument_id'))} ({bond_data.get('instrument_id')})")
    print(f"Evaluation date:              {result['evaluation_date']}")
    print(f"Settlement date:              {result['settlement_date']}")
    print(f"Maturity date:                {result['maturity_date']}")
    print(f"Discount curve:               {result.get('discount_curve_name', '-')}")
    print(f"Issuer spread:                {result['issuer_spread_bp']:.2f} bp")
    print(f"Index ratio (settlement):     {result['index_ratio_settlement']:.6f}")
    print(f"Index ratio (maturity):       {result['index_ratio_maturity']:.6f}")
    print(f"Inflation-accreted principal: {result['inflation_accreted_principal']:.4f}")
    print(f"Dirty price:                  {result['dirty_price']:.6f}")
    print(f"Accrued coupon:               {result['accrued_coupon']:.6f}")
    print(f"Clean price:                  {result['clean_price']:.6f}")
    if result['real_ytm'] is not None:
        print(f"Real YTM:                     {result['real_ytm'] * 100:.6f}%")
    if result['breakeven_inflation'] is not None:
        print(f"Breakeven inflation:          {result['breakeven_inflation'] * 100:.6f}%")
    if result.get('sensitivity'):
        print('Inflation rate sensitivity:')
        print(f"  {'Rate%':>10}  {'PV(Note)%':>12}")
        for s in result['sensitivity']:
            print(f"  {s['spread_bp']:>10.4f}  {s['pv_note_pct']:>12.6f}")
    print('Cashflows:')
    for cf in result['cashflows']:
        print(f"  {cf['date']}  {cf['type']:10s}  IR={cf['index_ratio']:.4f}  amount={cf['amount']:.4f}  pv={cf['pv']:.4f}")
    print()


def parse_args():
    parser = argparse.ArgumentParser(description='Price plain-vanilla inflation-linked bonds.')
    parser.add_argument('--bond-file', required=True, help='Path to bond JSON input file')
    parser.add_argument('--curve-file', default=str(CURVE_FILE), help='Path to curve JSON input file')
    parser.add_argument('--issuer-spread-bp', type=float, default=None)
    return parser.parse_args()


def main():
    args = parse_args()
    bond_data = load_json(Path(args.bond_file))
    curve_json = load_json(Path(args.curve_file))
    result = price_asset(bond_data, curve_json, issuer_spread_bp=args.issuer_spread_bp)
    print_result(bond_data, result)
    pdf_path = pdf_report.create_pdf_report(
        model_name='inflation_linked',
        instrument_id=bond_data.get('instrument_id', 'unknown'),
        input_payload=bond_data,
        output_payload=result,
    )
    print(f'PDF report: {pdf_path}')


if __name__ == '__main__':
    main()
