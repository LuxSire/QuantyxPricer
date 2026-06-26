"""Credit Default Swap pricer — protection vs premium legs.

Flat hazard rate implied from the mid-market CDS spread:
  λ = mid_spread / (1 − recovery_rate)

Survival probability:
  S(t) = exp(−λ × t)

Protection leg (present value of credit event payment):
  PV_prot = (1 − R) × Σ [S(t_{i−1}) − S(t_i)] × DF(t_mid_i)

Premium leg (present value of running spread payments):
  PV_prem = running_spread × Σ S(t_i) × α_i × DF(t_i)

NPV (protection buyer):  PV_prot − PV_prem
NPV (protection seller): PV_prem − PV_prot

Fair (par) spread:  PV_prot / risky_annuity  (in basis points)

Required JSON fields
--------------------
  instrument_id        ISIN or internal identifier
  evaluation_date      Pricing date (DD-MM-YYYY or YYYY-MM-DD)
  maturity_date        CDS maturity date
  notional             Notional amount
  mid_spread_bp        Market mid CDS spread (used to imply hazard rate)
  running_spread_bp    Contractual spread being paid (coupon)
  recovery_rate        Recovery rate assumption (decimal, e.g. 0.40)

Optional JSON fields
--------------------
  position             protection_buyer (default) | protection_seller
  reference_entity     Description of the reference entity
  coupon_frequency     Quarterly (default) | Semiannual | Annual
  day_count            Actual360 (default) | Actual365Fixed
  calendar             TARGET (default) | UnitedStates
  currency             USD (default) | EUR
"""

import argparse
import math
from pathlib import Path

import QuantLib as ql

from ..helper import (
    parse_date, get_day_count, get_calendar, get_business_day_convention,
    get_frequency, load_json, today_date_string,
    select_discount_curve_config, build_discount_curve,
    ASSETS_DIR, CURVES_DIR,
)

try:
    from reporting import pdf_report
except ModuleNotFoundError:
    import reporting.pdf_report as pdf_report

CURVE_FILE = CURVES_DIR / 'swap_curves.json'
ASSET_FILE = ASSETS_DIR / 'cds_example.json'


def _survival(hazard_rate, t):
    return math.exp(-hazard_rate * t)


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

    notional         = float(data.get('notional', 1_000_000.0))
    mid_spread       = float(data.get('mid_spread_bp', data.get('cds_spread_bp', 100.0))) / 10_000.0
    running_spread   = float(data.get('running_spread_bp', data.get('cds_spread_bp', 100.0))) / 10_000.0
    recovery_rate    = float(data.get('recovery_rate', 0.40))
    frequency        = data.get('coupon_frequency', 'Quarterly')
    position         = str(data.get('position', 'protection_buyer')).lower()

    maturity_date = parse_date(data['maturity_date'])
    freq          = get_frequency(frequency)

    schedule = ql.Schedule(
        evaluation_date, maturity_date,
        ql.Period(freq),
        cal, bdc, bdc,
        ql.DateGeneration.CDS, False,
    )
    dates = list(schedule)

    hazard_rate = mid_spread / max(1.0 - recovery_rate, 1e-6)

    pv_protection  = 0.0
    pv_premium     = 0.0
    risky_annuity  = 0.0
    periods        = []

    for i in range(1, len(dates)):
        t0 = dates[i - 1]
        t1 = dates[i]
        if t1 <= evaluation_date:
            continue

        alpha   = day_count.yearFraction(t0, t1)
        yr0     = day_count.yearFraction(evaluation_date, t0)
        yr1     = day_count.yearFraction(evaluation_date, t1)
        yr_mid  = 0.5 * (yr0 + yr1)

        s0      = _survival(hazard_rate, max(yr0, 0.0))
        s1      = _survival(hazard_rate, yr1)
        df_mid  = curve.discount(max(yr_mid, 1e-6))
        df1     = curve.discount(yr1)

        prot_cf    = (1.0 - recovery_rate) * (s0 - s1) * df_mid
        prem_cf    = running_spread * s1 * alpha * df1
        risky_ann  = s1 * alpha * df1

        pv_protection += prot_cf
        pv_premium    += prem_cf
        risky_annuity += risky_ann

        periods.append({
            'period_end':    t1.ISO(),
            'alpha':         alpha,
            'survival_prob': s1,
            'df':            df1,
            'pv_protection': prot_cf * notional,
            'pv_premium':    prem_cf * notional,
        })

    pv_protection *= notional
    pv_premium    *= notional
    risky_annuity_notional = risky_annuity * notional

    fair_spread_bp = (pv_protection / risky_annuity_notional * 10_000.0
                      if risky_annuity_notional > 0 else 0.0)
    dv01 = risky_annuity_notional * 1e-4

    if position == 'protection_buyer':
        npv = pv_protection - pv_premium
    else:
        npv = pv_premium - pv_protection

    return {
        'evaluation_date':     evaluation_date.ISO(),
        'maturity_date':       maturity_date.ISO(),
        'notional':            notional,
        'mid_spread_bp':       mid_spread * 10_000,
        'running_spread_bp':   running_spread * 10_000,
        'recovery_rate':       recovery_rate,
        'hazard_rate':         hazard_rate,
        'position':            position,
        'pv_protection_leg':   pv_protection,
        'pv_premium_leg':      pv_premium,
        'npv':                 npv,
        'risky_annuity':       risky_annuity,
        'fair_spread_bp':      fair_spread_bp,
        'dv01':                dv01,
        'periods':             periods,
        'curve_name':          curve_cfg.get('curve_name', ''),
    }


def print_result(data, result):
    print(f"{data.get('description', data.get('reference_entity', data.get('instrument_id', '')))} "
          f"({data.get('instrument_id', '')})")
    print(f"Evaluation date:     {result['evaluation_date']}")
    print(f"Maturity date:       {result['maturity_date']}")
    print(f"Notional:            {result['notional']:,.0f}")
    print(f"Position:            {result['position']}")
    print(f"Mid spread:          {result['mid_spread_bp']:.1f} bp")
    print(f"Running spread:      {result['running_spread_bp']:.1f} bp")
    print(f"Fair (par) spread:   {result['fair_spread_bp']:.2f} bp")
    print(f"Recovery rate:       {result['recovery_rate']:.1%}")
    print(f"Implied hazard rate: {result['hazard_rate']:.4%}")
    print(f"PV protection leg:   {result['pv_protection_leg']:,.2f}")
    print(f"PV premium leg:      {result['pv_premium_leg']:,.2f}")
    print(f"NPV ({result['position']}): {result['npv']:,.2f}")
    print(f"Risky annuity:       {result['risky_annuity']:.6f}")
    print(f"DV01:                {result['dv01']:,.2f}")
    print(f"Curve:               {result['curve_name']}")
    print()


def parse_args():
    parser = argparse.ArgumentParser(description='Price a Credit Default Swap.')
    parser.add_argument('--bond-file',  default=str(ASSET_FILE),  help='Path to CDS JSON')
    parser.add_argument('--curve-file', default=str(CURVE_FILE),  help='Path to swap curve JSON')
    return parser.parse_args()


if __name__ == '__main__':
    args       = parse_args()
    asset_data = load_json(Path(args.bond_file))
    curve_json = load_json(Path(args.curve_file))
    result     = price_asset(asset_data, curve_json)
    print_result(asset_data, result)
    pdf_path = pdf_report.create_pdf_report(
        model_name='cds',
        instrument_id=asset_data.get('instrument_id', 'unknown'),
        input_payload=asset_data,
        output_payload=result,
    )
    print(f'PDF report: {pdf_path}')
