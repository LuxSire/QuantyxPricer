"""Interest Rate Cap pricer — sum of Black caplets.

Each caplet pays max(F_i − K, 0) on the period notional at the payment date.

Black caplet formula (log-normal):
  d1 = [ln(F/K) + ½σ²T] / (σ√T)
  d2 = d1 − σ√T
  caplet_PV = notional × α_i × DF(T_i) × [F_i × N(d1) − K × N(d2)]

Normal (Bachelier) caplet formula:
  caplet_PV = notional × α_i × DF(T_i) × [(F−K) × N(z) + σ√T × n(z)]
  where z = (F − K) / (σ√T),  n() = standard normal PDF

Required JSON fields
--------------------
  instrument_id      ISIN or internal identifier
  evaluation_date    Pricing date (DD-MM-YYYY or YYYY-MM-DD)
  start_date         Effective start of the cap
  maturity_date      Final caplet payment date
  notional           Notional amount
  strike_rate        Cap strike rate (decimal or %)
  flat_vol           Flat implied volatility (decimal, e.g. 0.60 for 60%)
  coupon_frequency   Semiannual (default) | Quarterly | Annual

Optional JSON fields
--------------------
  vol_type           black (default) | normal
  day_count          Actual360 (default) | Actual365Fixed
  calendar           TARGET (default) | UnitedStates
  business_day_convention  ModifiedFollowing (default)
  currency           EUR (default) | USD
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
ASSET_FILE = ASSETS_DIR / 'cap_example.json'

_N  = ql.CumulativeNormalDistribution()
_SN = ql.NormalDistribution()


def _black_caplet(fwd, strike, vol, T, alpha, df, notional):
    """Black log-normal caplet PV."""
    if T <= 0 or vol <= 0 or fwd <= 0 or strike <= 0:
        return max(fwd - strike, 0.0) * alpha * df * notional
    sqrt_T = math.sqrt(T)
    d1 = (math.log(fwd / strike) + 0.5 * vol * vol * T) / (vol * sqrt_T)
    d2 = d1 - vol * sqrt_T
    return notional * alpha * df * (fwd * _N(d1) - strike * _N(d2))


def _normal_caplet(fwd, strike, vol, T, alpha, df, notional):
    """Bachelier normal caplet PV."""
    if T <= 0 or vol <= 0:
        return max(fwd - strike, 0.0) * alpha * df * notional
    sqrt_T = math.sqrt(T)
    z = (fwd - strike) / (vol * sqrt_T)
    return notional * alpha * df * ((fwd - strike) * _N(z) + vol * sqrt_T * _SN(z))


def price_asset(data, curve_json):
    evaluation_date = parse_date(data.get('evaluation_date', today_date_string()))
    ql.Settings.instance().evaluationDate = evaluation_date

    curve_cfg = select_discount_curve_config(curve_json, data)
    curve     = build_discount_curve(curve_cfg, evaluation_date)
    day_count = get_day_count(data.get('day_count', 'Actual360'))
    calendar  = data.get('calendar', 'TARGET')
    bdc_name  = data.get('business_day_convention', 'ModifiedFollowing')
    bdc       = get_business_day_convention(bdc_name)
    cal       = get_calendar(calendar)

    notional   = float(data.get('notional', 1_000_000.0))
    strike     = normalize_rate(data.get('strike_rate', 0.0))
    flat_vol   = float(data.get('flat_vol', 0.0))
    frequency  = data.get('coupon_frequency', 'Semiannual')
    vol_type   = str(data.get('vol_type', 'black')).lower()
    freq       = get_frequency(frequency)

    start_date    = parse_date(data['start_date'])
    maturity_date = parse_date(data['maturity_date'])

    schedule = ql.Schedule(
        start_date, maturity_date,
        ql.Period(freq),
        cal, bdc, bdc,
        ql.DateGeneration.Forward, False,
    )
    dates = list(schedule)

    total_pv  = 0.0
    caplets   = []
    pricer    = _black_caplet if vol_type == 'black' else _normal_caplet

    for i in range(1, len(dates)):
        t0 = dates[i - 1]
        t1 = dates[i]
        if t1 <= evaluation_date:
            continue

        # Fixing time = start of the period; payment at end
        T_fix  = day_count.yearFraction(evaluation_date, t0)
        T_fix  = max(T_fix, 0.0)
        alpha  = day_count.yearFraction(t0, t1)
        df0    = curve.discount(t0) if t0 > evaluation_date else 1.0
        df1    = curve.discount(t1)
        fwd    = (df0 / df1 - 1.0) / alpha if alpha > 0 else 0.0

        pv = pricer(fwd, strike, flat_vol, T_fix, alpha, df1, notional)
        total_pv += pv

        caplets.append({
            'period_start':  t0.ISO(),
            'period_end':    t1.ISO(),
            'fixing_time':   T_fix,
            'alpha':         alpha,
            'fwd_rate':      fwd,
            'strike':        strike,
            'df':            df1,
            'intrinsic':     max(fwd - strike, 0.0) * alpha * df1 * notional,
            'pv':            pv,
        })

    return {
        'evaluation_date':  evaluation_date.ISO(),
        'start_date':       start_date.ISO(),
        'maturity_date':    maturity_date.ISO(),
        'notional':         notional,
        'strike_rate':      strike,
        'flat_vol':         flat_vol,
        'vol_type':         vol_type,
        'npv':              total_pv,
        'num_caplets':      len(caplets),
        'caplets':          caplets,
        'curve_name':       curve_cfg.get('curve_name', ''),
    }


def print_result(data, result):
    print(f"{data.get('description', data.get('instrument_id', ''))} ({data.get('instrument_id', '')})")
    print(f"Evaluation date:  {result['evaluation_date']}")
    print(f"Start / Maturity: {result['start_date']}  →  {result['maturity_date']}")
    print(f"Notional:         {result['notional']:,.0f}")
    print(f"Strike (cap rate): {result['strike_rate']:.4%}")
    print(f"Flat vol ({result['vol_type']}): {result['flat_vol']:.4%}")
    print(f"Number of caplets: {result['num_caplets']}")
    print(f"NPV (cap):        {result['npv']:,.2f}")
    print(f"Curve:            {result['curve_name']}")
    if result['caplets']:
        print(f"\n{'Period end':<14} {'Fwd':>8} {'Strike':>8} {'T_fix':>6} {'DF':>8} {'PV':>12}")
        for c in result['caplets']:
            print(f"{c['period_end']:<14} {c['fwd_rate']:>8.4%} {c['strike']:>8.4%} "
                  f"{c['fixing_time']:>6.4f} {c['df']:>8.6f} {c['pv']:>12,.2f}")
    print()


def parse_args():
    parser = argparse.ArgumentParser(description='Price an Interest Rate Cap.')
    parser.add_argument('--bond-file',  default=str(ASSET_FILE),  help='Path to cap JSON')
    parser.add_argument('--curve-file', default=str(CURVE_FILE),  help='Path to swap curve JSON')
    return parser.parse_args()


if __name__ == '__main__':
    args       = parse_args()
    asset_data = load_json(Path(args.bond_file))
    curve_json = load_json(Path(args.curve_file))
    result     = price_asset(asset_data, curve_json)
    print_result(asset_data, result)
    pdf_path = pdf_report.create_pdf_report(
        model_name='cap',
        instrument_id=asset_data.get('instrument_id', 'unknown'),
        input_payload=asset_data,
        output_payload=result,
    )
    print(f'PDF report: {pdf_path}')
