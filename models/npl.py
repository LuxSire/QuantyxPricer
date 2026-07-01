"""Non-Performing Loan (NPL) portfolio pricer.

Prices a non-performing loan pool — or a single tranche carved out of a
securitised NPL pool (e.g. Italian GACS / Greek Hercules style structures) —
by:
  1. Splitting the Gross Book Value (GBV) into secured / unsecured buckets.
  2. Projecting net cash recoveries period-by-period using either an explicit
     cumulative recovery curve or a parametric (linear / S-curve) recovery
     ramp, net of special-servicer fees.
  3. Optionally carving out a cure_rate fraction of GBV that "cures" (returns
     to performing status during the workout) and is repaid in full as an
     early bullet, rather than running through the full liquidation timeline.
  4. If tranche fields are present, applying a sequential ABS-style waterfall
     (senior-first principal) to the pool's net recovery cash flows;
     otherwise the investor is treated as buying 100% of the pool.
  5. Discounting the resulting cash flows with a z-spread — an idiosyncratic
     NPL spread plus, optionally, a jurisdiction sovereign CDS spread stacked
     on top (same pattern as models/spire.py and models/index_linked.py) —
     over the selected benchmark curve.

What differs from models/abs.py
--------------------------------
abs.py models a *performing* pool that defaults gradually at a CDR over its
life, with scheduled interest/principal until default. npl.py instead starts
from a pool that is already 100% non-performing at the evaluation date —
there is no scheduled debt service and no CDR; all value comes from workout
recoveries on the legal claim (GBV), which is the standard NPL trading
convention. Price is quoted as % of GBV by default (or % of tranche_balance
if the pool is tranched).

Required JSON fields
--------------------
  instrument_id            ISIN or internal identifier
  evaluation_date          Pricing date (DD-MM-YYYY or YYYY-MM-DD)
  gbv                      Gross Book Value — total outstanding legal claim

Optional JSON fields — pool composition
----------------------------------------
  nbv                                Net Book Value after the seller's existing
                                      provisions (informational only)
  secured_pct                        Fraction of GBV secured by collateral
                                      (default 0 = fully unsecured)
  recovery_rate_secured              Ultimate recovery rate on secured GBV
                                      (default 0.45)
  recovery_rate_unsecured            Ultimate recovery rate on unsecured GBV
                                      (default 0.10)
  recovery_horizon_years_secured     Years to reach ultimate recovery, secured
                                      bucket (default 4)
  recovery_horizon_years_unsecured   Years to reach ultimate recovery, unsecured
                                      bucket (default 7)
  recovery_shape                     linear | s_curve (default s_curve)
  recovery_curve_secured             Explicit override:
                                      [{"year": 1, "cum_recovery_pct": 0.05}, ...]
  recovery_curve_unsecured           Explicit override, same shape
  recovery_frequency                 monthly | quarterly | semiannual | annual
                                      (default semiannual)

Optional JSON fields — workout frictions
------------------------------------------
  cure_rate                Fraction of GBV that cures instead of running the
                            recovery curve (default 0)
  cure_period_months       Month at which the cured balance is repaid in full
                            (default 12)
  cure_recovery_pct        Recovery rate applied to the cured balance
                            (default 1.0 = par)
  servicing_fee_pct        Special-servicer fee as % of gross recoveries
                            (default 0.03)
  legal_cost_pct_of_gbv    One-off upfront cost charged against GBV at t=0 —
                            due diligence, data-room, legal transfer costs
                            (default 0)

Optional JSON fields — tranching (omit for a whole-pool / pass-through buyer)
------------------------------------------------------------------------------
  tranche_balance       Outstanding notional of the tranche being priced
  tranche_coupon        Pass-through / certificate rate of the tranche
  senior_notes_balance  Balance of tranches senior to this one; paid first
                        from net recoveries each period (default 0)

Optional JSON fields — discounting & returns
-----------------------------------------------
  credit_spread_bp      Idiosyncratic NPL z-spread over the benchmark curve,
                        in basis points (default 0)
  cds_curve             Jurisdiction sovereign CDS curve name, stacked on top
                        of credit_spread_bp (default none — see
                        models/spire.py for the same pattern)
  purchase_price_pct    Price actually paid, % of GBV (or of tranche_balance
                        if tranched) — if supplied, IRR / MOIC are computed
                        relative to this price instead of the model NPV
  settlement_days       Days to settlement (default 2)
  calendar              TARGET | UnitedStates (default TARGET)
  currency              Settlement currency — used for discount curve selection
  description           Human-readable name
"""

import argparse
import math
from pathlib import Path

import QuantLib as ql

try:
    from models.helper import (
        today_date_string, parse_date, get_calendar,
        normalize_rate, load_json,
        select_discount_curve_config, build_discount_curve,
    )
except (ModuleNotFoundError, ImportError):
    from helper import (
        today_date_string, parse_date, get_calendar,
        normalize_rate, load_json,
        select_discount_curve_config, build_discount_curve,
    )

try:
    from reporting import pdf_report
except (ModuleNotFoundError, ImportError):
    import reporting.pdf_report as pdf_report

BASE_DIR     = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent
CURVE_FILE   = PROJECT_ROOT / 'curves' / 'swap_curves.json'

_FREQ_PER_YEAR = {'monthly': 12, 'quarterly': 4, 'semiannual': 2, 'annual': 1}


# ---------------------------------------------------------------------------
# CDS spread (jurisdiction sovereign risk) — same pattern as spire.py /
# index_linked.py
# ---------------------------------------------------------------------------

def _cds_flat_spread_bp(cds_curve_name, curve_json):
    """Return the flat CDS spread in basis points from the named curve (average of pillar rates)."""
    if not cds_curve_name:
        return 0.0
    curves = curve_json if isinstance(curve_json, list) else [curve_json]
    for c in curves:
        if c.get('curve_name') == cds_curve_name:
            pillars = c.get('pillars', [])
            if not pillars:
                return 0.0
            rates = [float(p.get('rate', 0.0)) for p in pillars]
            return sum(rates) / len(rates)
    return 0.0


# ---------------------------------------------------------------------------
# Recovery curve helpers
# ---------------------------------------------------------------------------

def _cum_recovery_pct(t_years, ultimate_rate, horizon_years, shape):
    """Parametric cumulative recovery curve, normalised to ultimate_rate at horizon_years."""
    if t_years <= 0 or horizon_years <= 0:
        return 0.0
    if t_years >= horizon_years:
        return ultimate_rate
    frac = t_years / horizon_years
    if shape == 'linear':
        return ultimate_rate * frac

    # s_curve: logistic ramp — slow start (legal filing), fastest around the
    # midpoint (auctions / settlements), tapering near the horizon.
    k, mid = 6.0, 0.5

    def logistic(x):
        return 1.0 / (1.0 + math.exp(-k * (x - mid)))

    f0, f1, f = logistic(0.0), logistic(1.0), logistic(frac)
    return ultimate_rate * (f - f0) / (f1 - f0)


def _interp_recovery_curve(curve_points, t_years):
    """Linearly interpolate an explicit [{'year', 'cum_recovery_pct'}] curve; flat-extend past the last point."""
    pts = sorted(curve_points, key=lambda p: float(p['year']))
    if not pts:
        return 0.0
    y0, r0 = float(pts[0]['year']), float(pts[0]['cum_recovery_pct'])
    if t_years <= y0:
        return r0 * (t_years / y0) if y0 > 0 else r0
    for i in range(1, len(pts)):
        y_prev, r_prev = float(pts[i - 1]['year']), float(pts[i - 1]['cum_recovery_pct'])
        y_next, r_next = float(pts[i]['year']),     float(pts[i]['cum_recovery_pct'])
        if t_years <= y_next:
            frac = (t_years - y_prev) / (y_next - y_prev) if y_next > y_prev else 0.0
            return r_prev + frac * (r_next - r_prev)
    return float(pts[-1]['cum_recovery_pct'])


def _bucket_cum_recovery(t_years, explicit_curve, ultimate_rate, horizon_years, shape):
    if explicit_curve:
        return _interp_recovery_curve(explicit_curve, t_years)
    return _cum_recovery_pct(t_years, ultimate_rate, horizon_years, shape)


# ---------------------------------------------------------------------------
# Pool cash flow generation
# ---------------------------------------------------------------------------

def _generate_pool_cashflows(bond_data, calendar, settlement_date, periods_per_year):
    gbv            = float(bond_data['gbv'])
    legal_cost_pct = float(bond_data.get('legal_cost_pct_of_gbv', 0.0))
    effective_gbv  = gbv * (1.0 - legal_cost_pct)

    secured_pct   = float(bond_data.get('secured_pct', 0.0))
    secured_gbv   = effective_gbv * secured_pct
    unsecured_gbv = effective_gbv * (1.0 - secured_pct)

    rate_secured      = normalize_rate(bond_data.get('recovery_rate_secured', 0.45))
    rate_unsecured    = normalize_rate(bond_data.get('recovery_rate_unsecured', 0.10))
    horizon_secured   = float(bond_data.get('recovery_horizon_years_secured', 4))
    horizon_unsecured = float(bond_data.get('recovery_horizon_years_unsecured', 7))
    shape             = str(bond_data.get('recovery_shape', 's_curve')).lower()

    curve_secured   = bond_data.get('recovery_curve_secured')
    curve_unsecured = bond_data.get('recovery_curve_unsecured')

    cure_rate         = float(bond_data.get('cure_rate', 0.0))
    cure_period_m      = int(bond_data.get('cure_period_months', 12))
    cure_recovery_pct  = float(bond_data.get('cure_recovery_pct', 1.0))

    servicing_fee_pct = float(bond_data.get('servicing_fee_pct', 0.03))

    months_per_period = max(1, 12 // periods_per_year)
    horizon_years      = max(horizon_secured, horizon_unsecured)
    n_periods          = max(1, int(math.ceil(horizon_years * periods_per_year)))

    # Cured balance is carved out up front and repaid as a separate bullet —
    # the recovery curve only runs on the remaining (non-cured) balance.
    cured_amount           = effective_gbv * cure_rate
    workout_secured_gbv    = secured_gbv   * (1.0 - cure_rate)
    workout_unsecured_gbv  = unsecured_gbv * (1.0 - cure_rate)

    rows = []
    prev_cum_secured   = 0.0
    prev_cum_unsecured = 0.0

    for p in range(1, n_periods + 1):
        t_years  = p * months_per_period / 12.0
        pay_date = calendar.advance(settlement_date, p * months_per_period, ql.Months)

        cum_secured   = _bucket_cum_recovery(t_years, curve_secured,   rate_secured,   horizon_secured,   shape)
        cum_unsecured = _bucket_cum_recovery(t_years, curve_unsecured, rate_unsecured, horizon_unsecured, shape)

        inc_secured   = max(0.0, cum_secured   - prev_cum_secured)
        inc_unsecured = max(0.0, cum_unsecured - prev_cum_unsecured)
        prev_cum_secured, prev_cum_unsecured = cum_secured, cum_unsecured

        gross_recovery = workout_secured_gbv * inc_secured + workout_unsecured_gbv * inc_unsecured
        servicing_fee  = gross_recovery * servicing_fee_pct

        rows.append({
            'date':            pay_date,
            'date_iso':        pay_date.ISO(),
            't_years':         t_years,
            'gross_recovery':  gross_recovery,
            'servicing_fee':   servicing_fee,
            'net_recovery':    gross_recovery - servicing_fee,
        })

    if cure_rate > 0:
        cure_date     = calendar.advance(settlement_date, cure_period_m, ql.Months)
        cure_gross    = cured_amount * cure_recovery_pct
        cure_fee      = cure_gross * servicing_fee_pct
        rows.append({
            'date':            cure_date,
            'date_iso':        cure_date.ISO(),
            't_years':         cure_period_m / 12.0,
            'gross_recovery':  cure_gross,
            'servicing_fee':   cure_fee,
            'net_recovery':    cure_gross - cure_fee,
        })
        rows.sort(key=lambda r: r['date'])

    return rows


# ---------------------------------------------------------------------------
# Sequential tranche waterfall (senior-first principal; no separate loss
# event since the recovery-rate assumptions already net out expected loss)
# ---------------------------------------------------------------------------

def _apply_tranche_waterfall(pool_rows, bond_data, periods_per_year):
    if 'tranche_balance' not in bond_data:
        return [{
            'date': r['date'], 'date_iso': r['date_iso'],
            'interest': 0.0, 'principal': r['net_recovery'],
            'tranche_cf': r['net_recovery'],
        } for r in pool_rows]

    tranche_balance  = float(bond_data['tranche_balance'])
    tranche_coupon   = normalize_rate(bond_data.get('tranche_coupon', 0.0))
    senior_remaining = float(bond_data.get('senior_notes_balance', 0.0))

    T = tranche_balance
    rows = []
    for r in pool_rows:
        if T <= 1e-8:
            break

        interest = T * tranche_coupon / periods_per_year

        avail = r['net_recovery']
        if senior_remaining > 0:
            to_senior        = min(avail, senior_remaining)
            senior_remaining = max(0.0, senior_remaining - to_senior)
            avail            = max(0.0, avail - to_senior)

        principal = min(avail, T)
        T = max(0.0, T - principal)

        rows.append({
            'date': r['date'], 'date_iso': r['date_iso'],
            'interest': interest, 'principal': principal,
            'tranche_cf': interest + principal,
        })
    return rows


# ---------------------------------------------------------------------------
# IRR solver (continuously compounded, Newton's method)
# ---------------------------------------------------------------------------

def _solve_irr(cashflows, evaluation_date, purchase_price):
    if purchase_price <= 0 or not cashflows:
        return 0.0
    day_count = ql.Actual365Fixed()

    def pv_at(r):
        total = 0.0
        for cf in cashflows:
            t = day_count.yearFraction(evaluation_date, cf['date'])
            if t > 0:
                total += cf['amount'] * math.exp(-r * t)
        return total

    r = 0.10
    for _ in range(100):
        diff = pv_at(r) - purchase_price
        if abs(diff) < 1e-6:
            break
        eps   = 1e-6
        deriv = (pv_at(r + eps) - pv_at(r)) / eps
        if abs(deriv) < 1e-10:
            break
        r -= diff / deriv
        r = max(-0.5, min(2.0, r))
    return r


# ---------------------------------------------------------------------------
# Core pricer
# ---------------------------------------------------------------------------

def price_sensitivity(bond_data, curve_json, n_steps=2, step_pct=0.10):
    """Sensitivity of price to the idiosyncratic credit_spread_bp; spread_bp
    reported is the total effective spread (CDS jurisdiction + idiosyncratic)."""
    cds_spread_bp = _cds_flat_spread_bp(bond_data.get('cds_curve'), curve_json)
    base_credit   = float(bond_data.get('credit_spread_bp', 0.0))
    multipliers   = [1.0 + (i - n_steps) * step_pct for i in range(2 * n_steps + 1)]
    sensitivity   = []
    for m in multipliers:
        credit_level = round(base_credit * m, 6)
        r = price_asset(bond_data, curve_json, credit_spread_bp=credit_level, _skip_sensitivity=True)
        sensitivity.append({
            'spread_bp':   round(credit_level + cds_spread_bp, 6),
            'pv_note_pct': r['price_pct']['pv_note'],
        })
    return sensitivity


def price_asset(bond_data, curve_json, credit_spread_bp=None, _skip_sensitivity=False):
    evaluation_date = parse_date(bond_data.get('evaluation_date', today_date_string()))
    ql.Settings.instance().evaluationDate = evaluation_date

    discount_curve_cfg  = select_discount_curve_config(curve_json, bond_data)
    curve                = build_discount_curve(discount_curve_cfg, evaluation_date)
    discount_curve_name  = discount_curve_cfg.get('curve_name')

    cds_spread_bp = _cds_flat_spread_bp(bond_data.get('cds_curve'), curve_json)
    if credit_spread_bp is None:
        credit_spread_bp = float(bond_data.get('credit_spread_bp', 0.0))
    z_spread = (credit_spread_bp + cds_spread_bp) / 10_000.0

    calendar        = get_calendar(bond_data.get('calendar', 'TARGET'))
    settlement_days = int(bond_data.get('settlement_days', 2))
    settlement_date = calendar.advance(evaluation_date, settlement_days, ql.Days)

    frequency        = str(bond_data.get('recovery_frequency', 'semiannual')).lower()
    periods_per_year = _FREQ_PER_YEAR.get(frequency, 2)

    pool_rows    = _generate_pool_cashflows(bond_data, calendar, settlement_date, periods_per_year)
    tranche_rows = _apply_tranche_waterfall(pool_rows, bond_data, periods_per_year)

    gbv           = float(bond_data['gbv'])
    notional_base = float(bond_data.get('tranche_balance', gbv))

    npv             = 0.0
    total_recovered = 0.0
    total_weighted  = 0.0
    cashflows       = []
    irr_cashflows   = []
    dc_act365       = ql.Actual365Fixed()

    for row in tranche_rows:
        d = row['date']
        if d <= evaluation_date:
            continue
        t  = dc_act365.yearFraction(evaluation_date, d)
        df = curve.discount(d) * math.exp(-z_spread * t)
        cf = row['tranche_cf']
        pv = cf * df
        npv += pv

        total_recovered += cf
        total_weighted   += cf * t

        cashflows.append({
            'date':      row['date_iso'],
            'interest':  row['interest'],
            'principal': row['principal'],
            'cf':        cf,
            'df':        df,
            'pv':        pv,
        })
        irr_cashflows.append({'date': d, 'amount': cf})

    wal       = total_weighted / total_recovered if total_recovered > 1e-8 else 0.0
    price_pct = npv / notional_base * 100.0 if notional_base > 0 else 0.0

    purchase_price_pct = bond_data.get('purchase_price_pct')
    purchase_price = (float(purchase_price_pct) / 100.0 * notional_base
                       if purchase_price_pct is not None else npv)
    irr  = _solve_irr(irr_cashflows, evaluation_date, purchase_price) if purchase_price > 0 else 0.0
    moic = total_recovered / purchase_price if purchase_price > 1e-8 else 0.0

    gross_recovered_total  = sum(r['gross_recovery'] for r in pool_rows)
    blended_recovery_rate  = gross_recovered_total / gbv if gbv > 0 else 0.0

    result = {
        'evaluation_date':       evaluation_date.ISO(),
        'settlement_date':       settlement_date.ISO(),
        'discount_curve_name':   discount_curve_name,
        'cds_curve_name':        bond_data.get('cds_curve', ''),
        'cds_spread_bp':         cds_spread_bp,
        'credit_spread_bp':      credit_spread_bp,
        'effective_spread_bp':   credit_spread_bp + cds_spread_bp,
        'gbv':                   gbv,
        'npv':                   npv,
        'selected_npv':          npv,
        'npv_to_maturity':       npv,
        'dirty_price':           npv,
        'clean_price':           npv,
        'total_recovered':       total_recovered,
        'wal':                   wal,
        'implied_irr':           irr,
        'moic':                  moic,
        'purchase_price':        purchase_price,
        'blended_recovery_rate': blended_recovery_rate,
        'cashflows':             cashflows,
        'price_pct': {
            'pv_note':              price_pct,
            'pv_note_to_maturity':  price_pct,
            'clean_price':          price_pct,
        },
    }
    if not _skip_sensitivity:
        result['sensitivity'] = price_sensitivity(bond_data, curve_json)
    return result


# ---------------------------------------------------------------------------
# CLI output
# ---------------------------------------------------------------------------

def print_result(bond_data, result):
    print(f"{bond_data.get('description', bond_data.get('instrument_id'))} "
          f"({bond_data.get('instrument_id')})")
    print(f"Evaluation date:       {result['evaluation_date']}")
    print(f"Settlement date:       {result['settlement_date']}")
    print(f"Discount curve:        {result.get('discount_curve_name', '-')}")
    print(f"CDS curve:             {result.get('cds_curve_name') or '-'}  ({result['cds_spread_bp']:.2f} bp)")
    print(f"Idiosyncratic spread:  {result['credit_spread_bp']:.2f} bp")
    print(f"Effective z-spread:    {result['effective_spread_bp']:.2f} bp")
    print()
    print(f"GBV:                   {result['gbv']:,.2f}")
    print(f"Secured %:             {float(bond_data.get('secured_pct', 0.0)) * 100:.1f}%")
    print(f"Recovery rate (sec.):  {normalize_rate(bond_data.get('recovery_rate_secured', 0.45)) * 100:.1f}%")
    print(f"Recovery rate (unsec.):{normalize_rate(bond_data.get('recovery_rate_unsecured', 0.10)) * 100:.1f}%")
    print(f"Blended recovery rate: {result['blended_recovery_rate'] * 100:.2f}%")
    print(f"Cure rate:             {float(bond_data.get('cure_rate', 0.0)) * 100:.1f}%")
    print()
    if 'tranche_balance' in bond_data:
        print(f"Tranche balance:       {float(bond_data['tranche_balance']):,.2f}")
        print(f"Tranche coupon:        {normalize_rate(bond_data.get('tranche_coupon', 0.0)) * 100:.4f}%")
    else:
        print("Pass-through (whole-pool) buyer — no tranching")
    print()
    print(f"NPV:                   {result['npv']:,.6f}")
    print(f"Price (% of base):     {result['price_pct']['pv_note']:.4f}%")
    print(f"Total recovered:       {result['total_recovered']:,.2f}")
    print(f"WAL:                   {result['wal']:.4f} years")
    print(f"Purchase price:        {result['purchase_price']:,.2f}")
    print(f"Implied IRR:           {result['implied_irr'] * 100:.2f}%")
    print(f"MOIC:                  {result['moic']:.2f}x")
    sensitivity = result.get('sensitivity')
    if sensitivity:
        base_bp = result['effective_spread_bp']
        print('Sensitivity (price %):')
        for s in sensitivity:
            marker = ' ◀' if abs(s['spread_bp'] - base_bp) < 0.01 else ''
            print(f"  {s['spread_bp']:>8.2f} bp  →  {s['pv_note_pct']:.6f}%{marker}")
    print()


def parse_args():
    parser = argparse.ArgumentParser(description='Price Non-Performing Loan (NPL) portfolios / tranches.')
    parser.add_argument('--bond-file',         required=True,           help='Path to instrument JSON')
    parser.add_argument('--curve-file',        default=str(CURVE_FILE), help='Path to curve JSON')
    parser.add_argument('--credit-spread-bp',  type=float, default=None)
    return parser.parse_args()


def main():
    args       = parse_args()
    bond_data  = load_json(Path(args.bond_file))
    curve_json = load_json(Path(args.curve_file))
    result     = price_asset(bond_data, curve_json, credit_spread_bp=args.credit_spread_bp)
    print_result(bond_data, result)
    pdf_path = pdf_report.create_pdf_report(
        model_name='npl',
        instrument_id=bond_data.get('instrument_id', 'unknown'),
        input_payload=bond_data,
        output_payload=result,
    )
    print(f'PDF report: {pdf_path}')


if __name__ == '__main__':
    main()
