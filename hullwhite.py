import json
import math
from pathlib import Path

import QuantLib as ql

CURVE_FILE = Path('eur_swap_curve.json')


def load_curve(path: Path):
    with open(path, 'r') as f:
        data = json.load(f)
    return data


def build_discount_curve(curve_json):
    calendar = ql.TARGET()
    ql.Settings.instance().evaluationDate = ql.Date(8, 6, 2026)
    day_count = ql.Actual365Fixed()
    settlement_days = 2
    fixed_leg_frequency = ql.Annual
    fixed_leg_convention = ql.Unadjusted
    fixed_leg_daycount = ql.Thirty360(ql.Thirty360.BondBasis)
    float_index = ql.Euribor6M()

    helpers = []
    for p in curve_json['pillars']:
        tenor = p['tenor']
        rate = p['rate']
        years = int(tenor.replace('Y', ''))
        helpers.append(
            ql.SwapRateHelper(
                ql.QuoteHandle(ql.SimpleQuote(rate)),
                ql.Period(years, ql.Years),
                calendar,
                fixed_leg_frequency,
                fixed_leg_convention,
                fixed_leg_daycount,
                float_index,
            )
        )

    curve = ql.PiecewiseLogCubicDiscount(ql.Settings.instance().evaluationDate, helpers, day_count)
    curve.enableExtrapolation()
    return curve


def price_bond(curve, spread_bp=175.0):
    eval_date = ql.Settings.instance().evaluationDate
    calendar = ql.TARGET()
    day_count = ql.Actual365Fixed()

    issue_date = ql.Date(4, 10, 2017)
    call_date = ql.Date(22, 9, 2027)

    coupon_rate = 0.0475
    par = 100.0
    spread = spread_bp / 10000.0

    schedule = ql.Schedule(
        eval_date,
        call_date,
        ql.Period(ql.Semiannual),
        calendar,
        ql.Unadjusted,
        ql.Unadjusted,
        ql.DateGeneration.Forward,
        False,
    )

    pv = 0.0
    cashflows = []
    for i in range(1, len(schedule)):
        d0 = schedule[i-1]
        d1 = schedule[i]
        accrual = day_count.yearFraction(d0, d1)
        cf = par * coupon_rate * accrual
        t = day_count.yearFraction(eval_date, d1)
        if t < 0:
            continue
        df = curve.discount(d1) * math.exp(-spread * t)
        pv_cf = cf * df
        pv += pv_cf
        cashflows.append((d1.ISO(), cf, df, pv_cf))

    t_call = day_count.yearFraction(eval_date, call_date)
    df_call = curve.discount(call_date) * math.exp(-spread * t_call)
    redemption = par * df_call
    pv += redemption

    return {
        'npv_to_first_call': pv,
        'redemption_pv': redemption,
        'spread_bp': spread_bp,
        'cashflows': cashflows,
    }


if __name__ == '__main__':
    curve_json = load_curve(CURVE_FILE)
    curve = build_discount_curve(curve_json)
    result = price_bond(curve, spread_bp=175.0)
    print('ABN AMRO XS1693822634 simplified QuantLib price to first call')
    print(f"NPV: {result['npv_to_first_call']:.4f}")
    print(f"Redemption PV: {result['redemption_pv']:.4f}")
    print(f"Spread: {result['spread_bp']:.1f} bp")
