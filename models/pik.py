"""PIK (Payment-in-Kind) bond pricer.

Covers instruments where interest is paid wholly or partly by accreting into
the outstanding principal rather than being paid in cash.  Common in leveraged
finance (high-yield, mezzanine, private credit) and in distressed situations.

Instrument types (instrument_type field — required)
----------------------------------------------------
  full_pik      All interest accretes each period; no cash coupons are paid.
                Final redemption = par × (1 + pik_rate/freq)^N.
                Equivalent to a compounding zero-coupon bond.

  pik_toggle    Issuer elects each period to pay cash or PIK (or a blend).
                The fraction elected as PIK is set by pik_election (0.0–1.0).
                Cash portion pays out as a normal coupon.
                PIK portion accretes into the running principal.
                If pik_rate_step_up is set, the PIK accrual rate exceeds the
                cash coupon rate by that spread (standard for toggle structures).

Accretion mechanics
-------------------
For each period [d0, d1] with opening principal P:

  pik_accreted = P × pik_rate  × pik_election       × yearFraction(d0, d1)
  cash_coupon  = P × coupon_rate × (1 − pik_election) × yearFraction(d0, d1)
  P_new        = P + pik_accreted

Final redemption = P_new after the last period.

Pricing
-------
  NPV = Σ cash_coupon(i) × DF(d_i) × exp(−z × t_i)
      + final_principal  × DF(maturity) × exp(−z × t_mat)

Required JSON fields
--------------------
  instrument_id    ISIN or internal identifier
  evaluation_date  Pricing date (DD-MM-YYYY or YYYY-MM-DD)
  issue_date       Issue / closing date (DD-MM-YYYY or YYYY-MM-DD)
  maturity_date    Maturity date (DD-MM-YYYY or YYYY-MM-DD)
  instrument_type  full_pik | pik_toggle
  coupon_rate      Base coupon / PIK accrual rate (decimal or %)
  coupon_frequency Annual | Semiannual | Quarterly | Monthly
  accrual_day_count  30/360 | Actual360 | Actual365Fixed | ACT/ACT (ICMA)
  calendar         TARGET | UnitedStates
  credit_spread_bp Z-spread over the discount curve in basis points

Optional JSON fields
--------------------
  par                 Face / initial principal value (default 100)
  pik_election        For pik_toggle: fraction of each coupon paid in kind
                      (0.0 = all cash, 1.0 = all PIK, default 1.0)
  pik_rate            Explicit PIK accrual rate if different from coupon_rate
                      (used when there is a step-up for electing PIK)
  pik_rate_step_up    Extra spread added to coupon_rate to obtain pik_rate
                      (e.g. 0.0075 for +75 bp; ignored if pik_rate is set)
  business_day_convention  ModifiedFollowing (default)
  date_generation     Backward (default)
  settlement_days     Days to settlement (default 2)
  first_coupon_date   Override for bonds with a short or long first coupon
  currency            Settlement currency — used for discount curve selection
  description         Human-readable name
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

BASE_DIR    = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent
CURVES_DIR  = PROJECT_ROOT / 'curves'
CURVE_FILE  = CURVES_DIR / 'swap_curves.json'

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
# Schedule
# ---------------------------------------------------------------------------

def _build_schedule(bond_data):
    issue_date    = parse_date(bond_data['issue_date'])
    maturity_date = parse_date(bond_data['maturity_date'])
    calendar      = get_calendar(bond_data.get('calendar', 'TARGET'))
    freq          = _FREQUENCIES.get(bond_data.get('coupon_frequency', 'Semiannual'), ql.Semiannual)
    bdc           = _BDC.get(bond_data.get('business_day_convention', 'ModifiedFollowing'), ql.ModifiedFollowing)
    gen           = _DATE_GEN.get(bond_data.get('date_generation', 'Backward'), ql.DateGeneration.Backward)

    first_coupon      = bond_data.get('first_coupon_date')
    first_coupon_date = parse_date(first_coupon) if first_coupon else ql.Date()

    return ql.Schedule(
        issue_date, maturity_date,
        ql.Period(freq),
        calendar, bdc, bdc, gen, False, first_coupon_date,
    ), maturity_date


# ---------------------------------------------------------------------------
# Period-by-period accretion
# ---------------------------------------------------------------------------

def _build_accretion(schedule, day_count, par, coupon_rate, pik_rate, pik_election):
    """Walk through the coupon schedule accumulating PIK into the running principal.

    Returns:
        cash_flows       list of { date, type, amount } for cash coupon payments
        accretion_rows   period-by-period detail
        final_principal  accreted principal at maturity (= redemption amount)
    """
    P          = float(par)
    cash_flows = []
    rows       = []

    for i in range(1, len(schedule)):
        d0      = schedule[i - 1]
        d1      = schedule[i]
        accrual = day_count.yearFraction(d0, d1)

        pik_accreted = P * pik_rate    * pik_election          * accrual
        cash_coupon  = P * coupon_rate * (1.0 - pik_election)  * accrual
        P_new        = P + pik_accreted

        rows.append({
            'period_start':      d0.ISO(),
            'period_end':        d1.ISO(),
            'opening_principal': P,
            'accrual':           round(accrual, 8),
            'pik_accreted':      pik_accreted,
            'cash_coupon':       cash_coupon,
            'closing_principal': P_new,
        })

        if cash_coupon > 0:
            cash_flows.append({'date': d1.ISO(), 'type': 'coupon_cash', 'amount': cash_coupon})

        P = P_new

    return cash_flows, rows, P


# ---------------------------------------------------------------------------
# Accrued cash interest (zero for full PIK; cash portion only for toggle)
# ---------------------------------------------------------------------------

def _accrued_interest(schedule, eval_date, settlement_date, day_count,
                      coupon_rate, pik_rate, pik_election, par):
    if pik_election >= 1.0:
        return 0.0

    P = float(par)
    for i in range(1, len(schedule)):
        d0      = schedule[i - 1]
        d1      = schedule[i]
        accrual = day_count.yearFraction(d0, d1)

        if d0 <= settlement_date < d1:
            t_acc = day_count.yearFraction(d0, settlement_date)
            return P * coupon_rate * (1.0 - pik_election) * t_acc

        P += P * pik_rate * pik_election * accrual

    return 0.0


# ---------------------------------------------------------------------------
# YTM solver (bisection, continuously compounded)
# ---------------------------------------------------------------------------

def _solve_ytm(cashflows, eval_date, npv, low=-0.5, high=2.0, tol=1e-9, max_iter=120):
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

def price_asset(bond_data, curve_json, issuer_spread_bp=None):
    evaluation_date = parse_date(bond_data.get('evaluation_date', today_date_string()))
    ql.Settings.instance().evaluationDate = evaluation_date

    discount_curve_cfg  = select_discount_curve_config(curve_json, bond_data)
    curve               = build_discount_curve(discount_curve_cfg, evaluation_date)
    discount_curve_name = discount_curve_cfg.get('curve_name')

    if issuer_spread_bp is None:
        issuer_spread_bp = float(bond_data.get('issuer_spread_bp',
                                                bond_data.get('credit_spread_bp', 0.0)))

    instrument_type = str(bond_data['instrument_type']).lower()
    par             = float(bond_data.get('par', 100.0))
    z_spread        = issuer_spread_bp / 10_000.0

    coupon_rate = normalize_rate(bond_data['coupon_rate'])

    # PIK accrual rate: may differ from coupon_rate for toggle bonds with a step-up
    if 'pik_rate' in bond_data:
        pik_rate = normalize_rate(bond_data['pik_rate'])
    else:
        step_up  = normalize_rate(bond_data.get('pik_rate_step_up', 0.0))
        pik_rate = coupon_rate + step_up

    # Election fraction
    if instrument_type == 'full_pik':
        pik_election = 1.0
    else:
        pik_election = float(bond_data.get('pik_election', 1.0))

    calendar        = get_calendar(bond_data.get('calendar', 'TARGET'))
    bdc             = _BDC.get(bond_data.get('business_day_convention', 'ModifiedFollowing'), ql.ModifiedFollowing)
    day_count       = get_day_count(bond_data.get('accrual_day_count', '30/360'))
    settlement_days = int(bond_data.get('settlement_days', 2))
    settlement_date = calendar.advance(evaluation_date, settlement_days, ql.Days, bdc)

    schedule, maturity_date = _build_schedule(bond_data)

    # --- accretion walk ---
    raw_cash_flows, accretion_rows, final_principal = _build_accretion(
        schedule, day_count, par, coupon_rate, pik_rate, pik_election,
    )

    # --- discount cash coupons ---
    pv_cash    = 0.0
    cashflows  = []
    for cf in raw_cash_flows:
        d = ql.DateParser.parseISO(cf['date'])
        if d <= evaluation_date:
            continue
        t    = ql.Actual365Fixed().yearFraction(evaluation_date, d)
        df   = curve.discount(d) * math.exp(-z_spread * t)
        pv   = cf['amount'] * df
        pv_cash += pv
        cashflows.append({**cf, 'df': df, 'pv': pv})

    # --- redemption (accreted principal at maturity) ---
    t_mat    = ql.Actual365Fixed().yearFraction(evaluation_date, maturity_date)
    df_mat   = curve.discount(maturity_date) * math.exp(-z_spread * t_mat)
    pv_redem = final_principal * df_mat
    cashflows.append({
        'date':   maturity_date.ISO(),
        'type':   'redemption',
        'amount': final_principal,
        'df':     df_mat,
        'pv':     pv_redem,
    })

    npv = pv_cash + pv_redem

    # --- accrued and clean price ---
    accrued     = _accrued_interest(schedule, evaluation_date, settlement_date,
                                    day_count, coupon_rate, pik_rate, pik_election, par)
    clean_price = npv - accrued

    # --- YTM ---
    ytm = _solve_ytm(cashflows, evaluation_date, npv)

    # --- total accretion ---
    total_pik_accreted = final_principal - par

    return {
        'selected_npv':          npv,
        'npv':                   npv,
        'npv_to_maturity':       npv,
        'npv_to_worst_call':     npv,
        'npv_to_first_call':     npv,
        'dirty_price':           npv,
        'clean_price':           clean_price,
        'accrued':               accrued,
        'initial_principal':     par,
        'final_principal':       final_principal,
        'total_pik_accreted':    total_pik_accreted,
        'coupon_rate':           coupon_rate,
        'pik_rate':              pik_rate,
        'pik_election':          pik_election,
        'ytm':                   ytm,
        'issuer_spread_bp':      issuer_spread_bp,
        'evaluation_date':       evaluation_date.ISO(),
        'maturity_date':         maturity_date.ISO(),
        'settlement_date':       settlement_date.ISO(),
        'discount_curve_name':   discount_curve_name,
        'cashflows':             cashflows,
        'accretion_schedule':    accretion_rows,
        'price_pct': {
            'pv_note':               npv / par * 100.0,
            'pv_note_to_maturity':   npv / par * 100.0,
            'pv_note_to_worst_call': npv / par * 100.0,
            'clean_price':           clean_price / par * 100.0,
        },
    }


# ---------------------------------------------------------------------------
# CLI output
# ---------------------------------------------------------------------------

def print_result(bond_data, result):
    par = float(bond_data.get('par', 100.0))
    print(f"{bond_data.get('description', bond_data.get('instrument_id'))} "
          f"({bond_data.get('instrument_id')})")
    print(f"Instrument type:      {bond_data.get('instrument_type')}")
    print(f"Evaluation date:      {result['evaluation_date']}")
    print(f"Settlement date:      {result['settlement_date']}")
    print(f"Maturity date:        {result['maturity_date']}")
    print(f"Discount curve:       {result.get('discount_curve_name', '-')}")
    print(f"Issuer spread:        {result['issuer_spread_bp']:.2f} bp")
    print()
    print(f"Coupon rate:          {result['coupon_rate'] * 100:.4f}%")
    print(f"PIK rate:             {result['pik_rate'] * 100:.4f}%")
    if result['pik_election'] < 1.0:
        print(f"PIK election:         {result['pik_election'] * 100:.1f}%")
    print()
    print(f"Initial principal:    {result['initial_principal']:,.4f}")
    print(f"Final principal:      {result['final_principal']:,.4f}")
    print(f"Total PIK accreted:   {result['total_pik_accreted']:,.4f}  "
          f"({result['total_pik_accreted'] / par * 100:.2f}%)")
    print()
    print(f"NPV (dirty):          {result['dirty_price']:,.6f}  "
          f"({result['dirty_price'] / par * 100:.4f}%)")
    if result['accrued']:
        print(f"Accrued:              {result['accrued']:,.6f}")
    print(f"Clean price:          {result['clean_price']:,.6f}  "
          f"({result['clean_price'] / par * 100:.4f}%)")
    if result['ytm'] is not None:
        print(f"YTM (cont. comp.):    {result['ytm'] * 100:.6f}%")
    print()
    print('Accretion schedule:')
    for row in result['accretion_schedule']:
        pik_str  = f"PIK={row['pik_accreted']:,.4f}" if row['pik_accreted'] else ''
        cash_str = f"cash={row['cash_coupon']:,.4f}"  if row['cash_coupon']  else ''
        parts    = '  '.join(p for p in [pik_str, cash_str] if p)
        print(f"  {row['period_end']}  P={row['closing_principal']:,.4f}  {parts}")
    print()


def parse_args():
    parser = argparse.ArgumentParser(description='Price PIK and PIK-toggle bonds.')
    parser.add_argument('--bond-file',        required=True,           help='Path to instrument JSON file')
    parser.add_argument('--curve-file',       default=str(CURVE_FILE), help='Path to curve JSON file')
    parser.add_argument('--issuer-spread-bp', type=float, default=None)
    return parser.parse_args()


def main():
    args       = parse_args()
    bond_data  = load_json(Path(args.bond_file))
    curve_json = load_json(Path(args.curve_file))
    result = price_asset(bond_data, curve_json, issuer_spread_bp=args.issuer_spread_bp)
    print_result(bond_data, result)
    pdf_path = pdf_report.create_pdf_report(
        model_name='pik',
        instrument_id=bond_data.get('instrument_id', 'unknown'),
        input_payload=bond_data,
        output_payload=result,
    )
    print(f'PDF report: {pdf_path}')


if __name__ == '__main__':
    main()
