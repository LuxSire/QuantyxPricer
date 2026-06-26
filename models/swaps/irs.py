"""Interest Rate Swap pricer — fixed vs floating legs.

Fixed leg:    Σ notional × fixed_rate × α_i × DF(t_i)
Floating leg: Σ notional × fwd(t_{i-1}, t_i) × α_i × DF(t_i)
              fwd(t_{i-1}, t_i) = (DF(t_{i-1}) / DF(t_i) − 1) / α_i

NPV (payer, paying fixed):    PV(float) − PV(fixed)
NPV (receiver, paying float): PV(fixed) − PV(float)

Required JSON fields
--------------------
  instrument_id      ISIN or internal identifier
  evaluation_date    Pricing date (DD-MM-YYYY or YYYY-MM-DD)
  start_date         Effective date of the swap
  maturity_date      Termination date
  notional           Notional amount
  fixed_rate         Fixed coupon rate (decimal or %)
  coupon_frequency   Annual | Semiannual | Quarterly | Monthly

Optional JSON fields
--------------------
  position           payer (default) | receiver
  floating_spread_bp Spread over the floating index in basis points (default 0)
  day_count          Actual365Fixed (default) | Actual360 | Thirty360
  calendar           TARGET (default) | UnitedStates
  business_day_convention  ModifiedFollowing (default)
  currency           EUR (default) | USD | GBP
"""

import argparse
import math
from pathlib import Path

import QuantLib as ql

from ..helper import (
    parse_date, get_day_count, get_calendar, get_business_day_convention,
    get_frequency, load_json, normalize_rate, today_date_string,
    select_discount_curve_config, build_discount_curve,
    ASSETS_DIR, CURVES_DIR,
)

try:
    from reporting import pdf_report
except ModuleNotFoundError:
    import reporting.pdf_report as pdf_report

CURVE_FILE = CURVES_DIR / 'swap_curves.json'
ASSET_FILE = ASSETS_DIR / 'irs_example.json'


def _build_schedule(start_date, end_date, frequency_name, calendar_name, bdc_name):
    cal = get_calendar(calendar_name)
    bdc = get_business_day_convention(bdc_name)
    freq = get_frequency(frequency_name)
    return ql.Schedule(
        start_date, end_date,
        ql.Period(freq),
        cal, bdc, bdc,
        ql.DateGeneration.Backward, False,
    )


def price_asset(data, curve_json):
    evaluation_date = parse_date(data.get('evaluation_date', today_date_string()))
    ql.Settings.instance().evaluationDate = evaluation_date

    curve_cfg = select_discount_curve_config(curve_json, data)
    curve     = build_discount_curve(curve_cfg, evaluation_date)
    day_count = get_day_count(data.get('day_count', 'Actual365Fixed'))
    calendar  = data.get('calendar', 'TARGET')
    bdc       = data.get('business_day_convention', 'ModifiedFollowing')

    notional      = float(data.get('notional', 1_000_000.0))
    fixed_rate    = normalize_rate(data.get('fixed_rate', 0.0))
    float_spread  = float(data.get('floating_spread_bp', 0.0)) / 10_000.0
    frequency     = data.get('coupon_frequency', 'Semiannual')
    position      = str(data.get('position', 'payer')).lower()

    start_date    = parse_date(data['start_date'])
    maturity_date = parse_date(data['maturity_date'])

    schedule = _build_schedule(start_date, maturity_date, frequency, calendar, bdc)
    dates    = list(schedule)

    pv_fixed      = 0.0
    pv_float      = 0.0
    annuity       = 0.0
    fwd_rate_sum  = 0.0   # Σ fwd × alpha × df  (no notional, no spread) for fair_rate
    fixed_cfs     = []
    float_cfs     = []

    for i in range(1, len(dates)):
        t0 = dates[i - 1]
        t1 = dates[i]
        if t1 <= evaluation_date:
            continue

        alpha  = day_count.yearFraction(t0, t1)
        df1    = curve.discount(t1)
        df0    = curve.discount(t0) if t0 > evaluation_date else 1.0
        fwd    = (df0 / df1 - 1.0) / alpha if alpha > 0 else 0.0

        fixed_cf  = notional * fixed_rate * alpha * df1
        float_cf  = notional * (fwd + float_spread) * alpha * df1

        pv_fixed     += fixed_cf
        pv_float     += float_cf
        annuity      += alpha * df1
        fwd_rate_sum += fwd * alpha * df1

        fixed_cfs.append({'date': t1.ISO(), 'rate': fixed_rate, 'alpha': alpha,
                          'df': df1, 'pv': fixed_cf})
        float_cfs.append({'date': t1.ISO(), 'fwd_rate': fwd, 'spread_bp': float_spread * 10_000,
                          'alpha': alpha, 'df': df1, 'pv': float_cf})

    fair_rate = fwd_rate_sum / annuity if annuity > 0 else 0.0
    dv01      = notional * annuity * 1e-4       # fixed-rate DV01 (1bp × annuity × notional)
    pv_bps    = (fair_rate - fixed_rate) * notional * annuity  # value of rate difference

    if position == 'payer':
        npv = pv_float - pv_fixed
    else:
        npv = pv_fixed - pv_float

    return {
        'evaluation_date':  evaluation_date.ISO(),
        'start_date':       start_date.ISO(),
        'maturity_date':    maturity_date.ISO(),
        'notional':         notional,
        'fixed_rate':       fixed_rate,
        'fair_rate':        fair_rate,
        'floating_spread_bp': float_spread * 10_000,
        'position':         position,
        'pv_fixed_leg':     pv_fixed,
        'pv_float_leg':     pv_float,
        'npv':              npv,
        'annuity':          annuity,
        'dv01':             dv01,
        'pv_bps':           pv_bps,
        'fixed_cashflows':  fixed_cfs,
        'float_cashflows':  float_cfs,
        'curve_name':       curve_cfg.get('curve_name', ''),
    }


def print_result(data, result):
    print(f"{data.get('description', data.get('instrument_id', ''))} ({data.get('instrument_id', '')})")
    print(f"Evaluation date:     {result['evaluation_date']}")
    print(f"Start / Maturity:    {result['start_date']}  →  {result['maturity_date']}")
    print(f"Notional:            {result['notional']:,.0f}")
    print(f"Position:            {result['position']}")
    print(f"Fixed rate:          {result['fixed_rate']:.4%}")
    print(f"Fair (mid-market) rate: {result['fair_rate']:.4%}")
    print(f"Floating spread:     {result['floating_spread_bp']:.1f} bp")
    print(f"PV fixed leg:        {result['pv_fixed_leg']:,.2f}")
    print(f"PV float leg:        {result['pv_float_leg']:,.2f}")
    print(f"NPV ({result['position']}): {result['npv']:,.2f}")
    print(f"Annuity (PV01):      {result['annuity']:.6f}")
    print(f"DV01:                {result['dv01']:,.2f}")
    print(f"P&L vs fair rate:    {result['pv_bps']:,.2f}")
    print(f"Curve:               {result['curve_name']}")
    print()


def parse_args():
    parser = argparse.ArgumentParser(description='Price an Interest Rate Swap.')
    parser.add_argument('--bond-file',  default=str(ASSET_FILE),  help='Path to IRS JSON')
    parser.add_argument('--curve-file', default=str(CURVE_FILE),  help='Path to swap curve JSON')
    return parser.parse_args()


if __name__ == '__main__':
    args       = parse_args()
    asset_data = load_json(Path(args.bond_file))
    curve_json = load_json(Path(args.curve_file))
    result     = price_asset(asset_data, curve_json)
    print_result(asset_data, result)
    pdf_path = pdf_report.create_pdf_report(
        model_name='irs',
        instrument_id=asset_data.get('instrument_id', 'unknown'),
        input_payload=asset_data,
        output_payload=result,
    )
    print(f'PDF report: {pdf_path}')
