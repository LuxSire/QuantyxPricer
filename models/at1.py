"""Additional Tier 1 (AT1 / CoCo) bond pricer.

Covers perpetual, callable subordinated instruments issued by banks under
Basel III / CRR2 capital rules.  Typical examples: EUR/USD AT1 issued by
European banks, contingent convertible capital notes (CoCos).

Key structural features handled
---------------------------------
  Perpetual             No fixed maturity; the bond continues indefinitely unless
                        called or a bail-in trigger fires.

  Fixed-to-reset        Fixed coupon from issue to first_call_date; at each call /
                        reset date the coupon resets to:
                          reset_coupon = reference_swap_rate(reset_tenor) + reset_spread
                        The reset_coupon is projected from the current forward swap
                        curve starting at first_call_date.

  Price-to-first-call   Market convention: the bond is priced assuming the issuer
                        calls at first_call_date.  YTC is the primary yield metric.

  Extension scenario    Price if NOT called: coupons continue at the projected
                        reset_coupon for perpetuity_horizon_years (default 50).
                        Extension risk = (npv_to_call − npv_to_perpetuity).

  Bail-in trigger       CET1 ratio threshold below which the principal is written
                        down or converted to equity.  Modelled as an informational
                        metric (distance_to_trigger); full structural modelling of
                        trigger probability is outside scope.

Pricing formula
---------------
  npv_to_call = Σ [ par × coupon × accrual × DF(d) × exp(−z × t) ]   (coupon leg)
              + par × DF(first_call_date) × exp(−z × t_call)           (redemption)

  npv_to_perpetuity = coupon_leg_to_call
                    + Σ [ par × reset_coupon × accrual × DF(d) × exp(−z × t) ]
                      (extension leg over perpetuity_horizon_years)

Required JSON fields
--------------------
  instrument_id        ISIN or internal identifier
  description          Human-readable name (e.g. 'BNP 6.625% AT1 Perp NC5')
  evaluation_date      Pricing date (DD-MM-YYYY or YYYY-MM-DD)
  issue_date           Issue date (DD-MM-YYYY or YYYY-MM-DD)
  first_call_date      First optional call / reset date (DD-MM-YYYY or YYYY-MM-DD)
  fixed_coupon_rate    Fixed coupon rate until first call (decimal or %)
  reset_spread         Spread over the reference swap rate at each reset (decimal or bp)
  reset_reference_tenor  Tenor of the reference swap rate for reset (e.g. '5Y', '10Y')
  cet1_trigger_pct     CET1 ratio (%) below which bail-in fires (e.g. 5.125 or 7.0)
  loss_absorption      write_down | equity_conversion
  accrual_day_count    ACT/ACT (ICMA) | 30/360 | Actual365Fixed
  coupon_frequency     Annual | Semiannual
  calendar             TARGET | UnitedStates
  credit_spread_bp     Z-spread over the discount curve in basis points

Optional JSON fields
--------------------
  par                    Face value (default 100)
  business_day_convention  ModifiedFollowing (default)
  date_generation        Backward (default)
  settlement_days        Days to settlement (default 2)
  cet1_current_pct       Current CET1 ratio (%) — used to compute distance_to_trigger
  conversion_price       For equity_conversion: share price floor (informational)
  call_frequency_years   Interval between call dates after first call (default 5)
  price_convention       to_first_call (default) | to_perpetuity
  perpetuity_horizon_years  Years to model in extension scenario (default 50)
  hw_a                   Hull-White mean reversion speed (default 0.03)
  hw_sigma               Hull-White short-rate volatility (default 0.01)
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
        tenor_to_period,
    )
except (ModuleNotFoundError, ImportError):
    from helper import (
        today_date_string, parse_date, get_calendar, get_day_count,
        normalize_rate, load_json,
        select_discount_curve_config, build_discount_curve,
        tenor_to_period,
    )

try:
    from reporting import pdf_report
except (ModuleNotFoundError, ImportError):
    import reporting.pdf_report as pdf_report

BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent
CURVES_DIR = PROJECT_ROOT / 'curves'
CURVE_FILE = CURVES_DIR / 'swap_curves.json'

_FREQUENCIES = {
    'Annual':     ql.Annual,
    'Semiannual': ql.Semiannual,
    'Quarterly':  ql.Quarterly,
    'Monthly':    ql.Monthly,
}

_BDC = {
    'ModifiedFollowing': ql.ModifiedFollowing,
    'Following':         ql.Following,
    'Unadjusted':        ql.Unadjusted,
}

_DATE_GEN = {
    'Backward': ql.DateGeneration.Backward,
    'Forward':  ql.DateGeneration.Forward,
}


# ---------------------------------------------------------------------------
# Schedule builder (issue → call date only; no maturity)
# ---------------------------------------------------------------------------

def _build_schedule(bond_data, end_date):
    issue_date = parse_date(bond_data['issue_date'])
    calendar   = get_calendar(bond_data.get('calendar', 'TARGET'))
    freq       = _FREQUENCIES.get(bond_data.get('coupon_frequency', 'Annual'), ql.Annual)
    bdc        = _BDC.get(bond_data.get('business_day_convention', 'ModifiedFollowing'), ql.ModifiedFollowing)
    gen        = _DATE_GEN.get(bond_data.get('date_generation', 'Backward'), ql.DateGeneration.Backward)

    first_coupon = bond_data.get('first_coupon_date')
    first_coupon_date = parse_date(first_coupon) if first_coupon else ql.Date()

    return ql.Schedule(
        issue_date, end_date,
        ql.Period(freq),
        calendar, bdc, bdc,
        gen, False, first_coupon_date,
    )


# ---------------------------------------------------------------------------
# Fixed coupon leg price-to-call
# ---------------------------------------------------------------------------

def _price_coupon_leg(bond_data, curve, schedule, call_date, z_spread, par,
                      coupon_rate, day_count, eval_date, include_redemption=True):
    cashflows = []
    pv = 0.0

    for i in range(1, len(schedule)):
        d0 = schedule[i - 1]
        d1 = schedule[i]
        if d1 <= eval_date:
            continue
        if d1 > call_date:
            break

        accrual    = day_count.yearFraction(d0, d1)
        coupon_cf  = par * coupon_rate * accrual
        t          = ql.Actual365Fixed().yearFraction(eval_date, d1)
        df         = curve.discount(d1) * math.exp(-z_spread * t)
        pv_cf      = coupon_cf * df
        pv        += pv_cf
        cashflows.append({
            'date': d1.ISO(), 'type': 'coupon',
            'rate': coupon_rate, 'amount': coupon_cf, 'df': df, 'pv': pv_cf,
        })

    if include_redemption:
        t_call    = ql.Actual365Fixed().yearFraction(eval_date, call_date)
        df_call   = curve.discount(call_date) * math.exp(-z_spread * t_call)
        pv_par    = par * df_call
        pv       += pv_par
        cashflows.append({
            'date': call_date.ISO(), 'type': 'redemption',
            'rate': None, 'amount': par, 'df': df_call, 'pv': pv_par,
        })

    return pv, cashflows


# ---------------------------------------------------------------------------
# Reset coupon: forward swap rate at first_call_date + reset_spread
# ---------------------------------------------------------------------------

def _project_reset_coupon(bond_data, curve, first_call_date, eval_date):
    reset_tenor  = bond_data.get('reset_reference_tenor', '5Y')
    reset_spread = normalize_rate(bond_data.get('reset_spread', 0.0))
    calendar     = get_calendar(bond_data.get('calendar', 'TARGET'))
    bdc          = _BDC.get(bond_data.get('business_day_convention', 'ModifiedFollowing'), ql.ModifiedFollowing)
    freq         = _FREQUENCIES.get(bond_data.get('coupon_frequency', 'Annual'), ql.Annual)
    day_count    = get_day_count(bond_data.get('accrual_day_count', 'ACT/ACT (ICMA)'))

    tenor_period = tenor_to_period(reset_tenor)
    swap_end     = calendar.advance(first_call_date, tenor_period, bdc)

    # Annuity of the fixed leg of a swap starting at first_call_date
    fixed_schedule = ql.Schedule(
        first_call_date, swap_end,
        ql.Period(freq),
        calendar, bdc, bdc,
        ql.DateGeneration.Forward, False,
    )

    annuity = 0.0
    for i in range(1, len(fixed_schedule)):
        f0 = fixed_schedule[i - 1]
        f1 = fixed_schedule[i]
        alpha    = day_count.yearFraction(f0, f1)
        annuity += alpha * curve.discount(f1)

    if annuity <= 0.0:
        ref_swap_rate = 0.0
    else:
        df_start      = curve.discount(first_call_date)
        df_end        = curve.discount(swap_end)
        ref_swap_rate = (df_start - df_end) / annuity

    return ref_swap_rate + reset_spread, ref_swap_rate, reset_spread


# ---------------------------------------------------------------------------
# Extension leg: coupons at reset_coupon for perpetuity_horizon_years
# ---------------------------------------------------------------------------

def _price_extension_leg(bond_data, curve, first_call_date, reset_coupon,
                         z_spread, par, day_count, eval_date):
    calendar       = get_calendar(bond_data.get('calendar', 'TARGET'))
    bdc            = _BDC.get(bond_data.get('business_day_convention', 'ModifiedFollowing'), ql.ModifiedFollowing)
    freq           = _FREQUENCIES.get(bond_data.get('coupon_frequency', 'Annual'), ql.Annual)
    horizon_years  = int(bond_data.get('perpetuity_horizon_years', 50))

    ext_end      = calendar.advance(first_call_date, ql.Period(horizon_years, ql.Years), bdc)
    ext_schedule = ql.Schedule(
        first_call_date, ext_end,
        ql.Period(freq),
        calendar, bdc, bdc,
        ql.DateGeneration.Forward, False,
    )

    pv = 0.0
    for i in range(1, len(ext_schedule)):
        d0        = ext_schedule[i - 1]
        d1        = ext_schedule[i]
        accrual   = day_count.yearFraction(d0, d1)
        coupon_cf = par * reset_coupon * accrual
        t         = ql.Actual365Fixed().yearFraction(eval_date, d1)
        df        = curve.discount(d1) * math.exp(-z_spread * t)
        pv       += coupon_cf * df

    return pv


# ---------------------------------------------------------------------------
# Accrued coupon
# ---------------------------------------------------------------------------

def _accrued_coupon(schedule, call_date, eval_date, settlement_date, day_count, coupon_rate, par):
    for i in range(1, len(schedule)):
        d0 = schedule[i - 1]
        d1 = min(schedule[i], call_date)
        if d0 <= settlement_date < d1:
            return par * coupon_rate * day_count.yearFraction(d0, settlement_date)
    return 0.0


# ---------------------------------------------------------------------------
# YTC solver (bisection, continuously compounded)
# ---------------------------------------------------------------------------

def _solve_ytc(cashflows, eval_date, npv, low=-0.5, high=2.0, tol=1e-9, max_iter=120):
    if npv <= 0 or not cashflows:
        return None

    def pv_at(y):
        total = 0.0
        for cf in cashflows:
            d = ql.DateParser.parseISO(cf['date'])
            t = ql.Actual365Fixed().yearFraction(eval_date, d)
            if t > 0:
                total += cf['amount'] * math.exp(-y * t)
        return total

    f_low  = pv_at(low)  - npv
    f_high = pv_at(high) - npv

    for _ in range(30):
        if f_low * f_high <= 0:
            break
        high  += 0.5
        f_high = pv_at(high) - npv

    if f_low * f_high > 0:
        return None

    for _ in range(max_iter):
        mid   = 0.5 * (low + high)
        f_mid = pv_at(mid) - npv
        if abs(f_mid) < tol:
            return mid
        if f_low * f_mid <= 0:
            high   = mid
            f_high = f_mid
        else:
            low   = mid
            f_low = f_mid

    return 0.5 * (low + high)


# ---------------------------------------------------------------------------
# Core pricer
# ---------------------------------------------------------------------------

def price_sensitivity(bond_data, curve_json, n_steps=2, step_pct=0.10):
    base = float(bond_data.get('issuer_spread_bp', bond_data.get('credit_spread_bp', 0.0)))
    multipliers = [1.0 + (i - n_steps) * step_pct for i in range(2 * n_steps + 1)]
    sensitivity = []
    for m in multipliers:
        level = round(base * m, 6)
        r = price_asset(bond_data, curve_json, issuer_spread_bp=level, _skip_sensitivity=True)
        sensitivity.append({'spread_bp': level, 'pv_note_pct': r['price_pct']['pv_note']})
    return sensitivity


def price_asset(bond_data, curve_json, issuer_spread_bp=None, _skip_sensitivity=False):
    evaluation_date = parse_date(bond_data.get('evaluation_date', today_date_string()))
    ql.Settings.instance().evaluationDate = evaluation_date

    discount_curve_cfg  = select_discount_curve_config(curve_json, bond_data)
    curve               = build_discount_curve(discount_curve_cfg, evaluation_date)
    discount_curve_name = discount_curve_cfg.get('curve_name')

    if issuer_spread_bp is None:
        issuer_spread_bp = float(bond_data.get('issuer_spread_bp',
                                                bond_data.get('credit_spread_bp', 0.0)))

    par            = float(bond_data.get('par', 100.0))
    z_spread       = issuer_spread_bp / 10_000.0
    fixed_coupon   = normalize_rate(bond_data['fixed_coupon_rate'])
    _first_call_raw = (
        bond_data.get('first_call_date')
        or (bond_data['call_dates'][0] if bond_data.get('call_dates') else None)
    )
    if not _first_call_raw:
        raise ValueError('at1 model requires first_call_date or at least one entry in call_dates')
    first_call_date = parse_date(_first_call_raw)
    calendar       = get_calendar(bond_data.get('calendar', 'TARGET'))
    bdc            = _BDC.get(bond_data.get('business_day_convention', 'ModifiedFollowing'), ql.ModifiedFollowing)
    day_count      = get_day_count(bond_data.get('accrual_day_count', 'ACT/ACT (ICMA)'))
    settlement_days = int(bond_data.get('settlement_days', 2))
    settlement_date = calendar.advance(evaluation_date, settlement_days, ql.Days, bdc)

    # Build schedule from issue to first call
    schedule = _build_schedule(bond_data, first_call_date)

    # --- price to first call (primary) ---
    npv_to_call, cashflows = _price_coupon_leg(
        bond_data, curve, schedule, first_call_date,
        z_spread, par, fixed_coupon, day_count, evaluation_date,
        include_redemption=True,
    )

    # --- accrued coupon and clean price ---
    accrued        = _accrued_coupon(schedule, first_call_date, evaluation_date,
                                     settlement_date, day_count, fixed_coupon, par)
    clean_price    = npv_to_call - accrued

    # --- YTC (continuously compounded, basis Actual/365) ---
    ytc = _solve_ytc(cashflows, evaluation_date, npv_to_call)

    # --- reset coupon projected from forward swap curve ---
    reset_coupon, ref_swap_rate, reset_spread = _project_reset_coupon(
        bond_data, curve, first_call_date, evaluation_date
    )

    # --- extension scenario ---
    # Coupon leg to call without redemption + extension coupons at reset_coupon
    pv_coupon_leg_to_call, _ = _price_coupon_leg(
        bond_data, curve, schedule, first_call_date,
        z_spread, par, fixed_coupon, day_count, evaluation_date,
        include_redemption=False,
    )
    pv_extension_leg = _price_extension_leg(
        bond_data, curve, first_call_date, reset_coupon,
        z_spread, par, day_count, evaluation_date,
    )
    npv_to_perpetuity = pv_coupon_leg_to_call + pv_extension_leg

    # Extension risk: difference between the two scenarios (positive → call is preferred by investor)
    extension_risk_pct = (npv_to_call - npv_to_perpetuity) / par * 100.0

    # --- bail-in metrics ---
    cet1_trigger  = float(bond_data.get('cet1_trigger_pct', 5.125))
    cet1_current  = bond_data.get('cet1_current_pct')
    distance_to_trigger = (float(cet1_current) - cet1_trigger) if cet1_current is not None else None

    # --- selected NPV ---
    price_convention = str(bond_data.get('price_convention', 'to_first_call')).lower()
    selected_npv = npv_to_perpetuity if price_convention == 'to_perpetuity' else npv_to_call

    result = {
        'selected_npv':          selected_npv,
        'npv_to_first_call':     npv_to_call,
        'npv_to_perpetuity':     npv_to_perpetuity,
        'npv_to_worst_call':     npv_to_call,    # aliases for pricer.py compatibility
        'npv_to_maturity':       npv_to_call,
        'dirty_price':           selected_npv,
        'clean_price':           clean_price,
        'accrued_coupon':        accrued,
        'ytc':                   ytc,
        'fixed_coupon_rate':     fixed_coupon,
        'reset_coupon_rate':     reset_coupon,
        'reference_swap_rate':   ref_swap_rate,
        'reset_spread':          reset_spread,
        'extension_risk_pct':    extension_risk_pct,
        'cet1_trigger_pct':      cet1_trigger,
        'cet1_current_pct':      float(cet1_current) if cet1_current is not None else None,
        'distance_to_trigger':   distance_to_trigger,
        'loss_absorption':       bond_data.get('loss_absorption', 'write_down'),
        'issuer_spread_bp':      issuer_spread_bp,
        'evaluation_date':       evaluation_date.ISO(),
        'first_call_date':       first_call_date.ISO(),
        'settlement_date':       settlement_date.ISO(),
        'discount_curve_name':   discount_curve_name,
        'cashflows':             cashflows,
        'price_pct': {
            'pv_note':               selected_npv / par * 100.0,
            'pv_note_to_worst_call': npv_to_call / par * 100.0,
            'pv_note_to_maturity':   npv_to_call / par * 100.0,
            'clean_price':           clean_price / par * 100.0,
        },
    }
    if not _skip_sensitivity:
        result['sensitivity'] = price_sensitivity(bond_data, curve_json)
    return result


# ---------------------------------------------------------------------------
# CLI output
# ---------------------------------------------------------------------------

def print_report(bond_data, result):
    par = float(bond_data.get('par', 100.0))
    print(f"{bond_data.get('description', bond_data.get('instrument_id'))} "
          f"({bond_data.get('instrument_id')})")
    print(f"Evaluation date:       {result['evaluation_date']}")
    print(f"Settlement date:       {result['settlement_date']}")
    print(f"First call date:       {result['first_call_date']}")
    print(f"Discount curve:        {result.get('discount_curve_name', '-')}")
    print(f"Issuer spread:         {result['issuer_spread_bp']:.2f} bp")
    print(f"Fixed coupon:          {result['fixed_coupon_rate'] * 100:.4f}%")
    print(f"Reset reference rate:  {result['reference_swap_rate'] * 100:.4f}%  "
          f"(tenor {bond_data.get('reset_reference_tenor', '5Y')})")
    print(f"Reset spread:          {result['reset_spread'] * 100:.4f}%")
    print(f"Projected reset coupon:{result['reset_coupon_rate'] * 100:.4f}%")
    print()
    print(f"NPV to first call:     {result['npv_to_first_call']:.6f}  "
          f"({result['npv_to_first_call'] / par * 100:.4f}%)")
    print(f"Clean price to call:   {result['clean_price']:.6f}  "
          f"({result['clean_price'] / par * 100:.4f}%)")
    print(f"Accrued coupon:        {result['accrued_coupon']:.6f}")
    print(f"NPV to perpetuity:     {result['npv_to_perpetuity']:.6f}  "
          f"({result['npv_to_perpetuity'] / par * 100:.4f}%)")
    print(f"Extension risk:        {result['extension_risk_pct']:.4f}%  "
          f"({'gain' if result['extension_risk_pct'] > 0 else 'loss'} vs not-called)")
    if result['ytc'] is not None:
        print(f"YTC (cont. comp.):     {result['ytc'] * 100:.6f}%")
    print()
    print(f"Loss absorption:       {result['loss_absorption']}")
    print(f"CET1 trigger:          {result['cet1_trigger_pct']:.3f}%")
    if result['cet1_current_pct'] is not None:
        print(f"CET1 current:          {result['cet1_current_pct']:.3f}%")
    if result['distance_to_trigger'] is not None:
        print(f"Distance to trigger:   {result['distance_to_trigger']:.3f}pp")
    sensitivity = result.get('sensitivity')
    if sensitivity:
        base_bp = float(bond_data.get('issuer_spread_bp', bond_data.get('credit_spread_bp', 0.0)))
        print('Sensitivity (price %):')
        for s in sensitivity:
            marker = ' ◀' if abs(s['spread_bp'] - base_bp) < 0.01 else ''
            print(f"  {s['spread_bp']:>8.2f} bp  →  {s['pv_note_pct']:.6f}%{marker}")
    print()


def parse_args():
    parser = argparse.ArgumentParser(description='Price AT1 / CoCo bonds.')
    parser.add_argument('--bond-file',         required=True,          help='Path to instrument JSON file')
    parser.add_argument('--curve-file',        default=str(CURVE_FILE), help='Path to curve JSON file')
    parser.add_argument('--issuer-spread-bp',  type=float,  default=None)
    return parser.parse_args()


def main():
    args       = parse_args()
    bond_data  = load_json(Path(args.bond_file))
    curve_json = load_json(Path(args.curve_file))
    result = price_asset(bond_data, curve_json, issuer_spread_bp=args.issuer_spread_bp)
    print_result(bond_data, result)
    pdf_path = pdf_report.create_pdf_report(
        model_name='at1',
        instrument_id=bond_data.get('instrument_id', 'unknown'),
        input_payload=bond_data,
        output_payload=result,
    )
    print(f'PDF report: {pdf_path}')


if __name__ == '__main__':
    main()
