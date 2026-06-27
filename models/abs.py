"""Asset-Backed Security (ABS) and Mortgage-Backed Security (MBS) pricer.

Prices a single ABS/MBS tranche by:
  1. Generating monthly pool-level cash flows using a CPR or PSA prepayment
     model with CDR-based defaults and a recovery lag.
  2. Applying a sequential waterfall to derive the tranche's cash flows after
     credit support absorbs losses from below.
  3. Discounting the resulting tranche cash flows with a z-spread over the
     selected benchmark curve.

Instrument types (instrument_type field — required)
----------------------------------------------------
  abs              Generic ABS: auto loans, credit card, student loans.
                   Flat CPR prepayment model; CDR + loss severity for credit.

  mbs_agency       Agency MBS: Fannie Mae, Freddie Mac, Ginnie Mae pass-throughs.
                   PSA prepayment ramp by default (psa_speed = 100 PSA).
                   No credit risk (CDR = 0, government / agency guarantee).

  mbs_non_agency   Non-agency / private-label MBS.
                   PSA or CPR prepayment; CDR + loss severity for credit.

Pool cash flow mechanics (monthly)
-----------------------------------
For each month m with opening pool balance B:

  interest(m)       = B × WAC / 12
  scheduled_P(m)    = PMT(WAC/12, months_remaining, B) − interest(m)
  SMM(m)            = 1 − (1 − CPR(m))^(1/12)      [single monthly mortality]
  prepayment(m)     = (B − scheduled_P) × SMM
  MDR(m)            = 1 − (1 − CDR)^(1/12)
  defaults(m)       = B × MDR
  losses(m)         = defaults × loss_severity
  recovery(m)       = defaults delayed by recovery_lag_months × (1 − loss_severity)
  pool_principal(m) = scheduled_P + prepayment + recovery
  B_next            = B − scheduled_P − prepayment − defaults

PSA prepayment ramp
-------------------
For month m (1-indexed pool-relative, adjusting for seasoning):
  aged = pool_seasoning + m
  CPR_base = min(aged / 30, 1.0) × 6%
  CPR(m)   = CPR_base × psa_speed / 100

Tranche waterfall (sequential)
-------------------------------
  1. Credit support (subordination) absorbs pool losses first.
  2. If senior_notes_balance > 0, senior tranches receive principal first
     until their balance is retired.
  3. This tranche receives remaining principal up to its outstanding balance.
  4. Any loss in excess of credit support reduces the tranche balance.

Required JSON fields
--------------------
  instrument_id    ISIN or internal identifier
  evaluation_date  Pricing date (DD-MM-YYYY or YYYY-MM-DD)
  instrument_type  abs | mbs_agency | mbs_non_agency
  pool_balance     Current outstanding pool balance
  pool_wac         Weighted average coupon of the pool (decimal or %)
  pool_wam         Weighted average remaining maturity in months (integer)
  tranche_balance  Outstanding notional of the tranche being priced
  tranche_coupon   Pass-through / certificate rate of the tranche (decimal or %)
  credit_spread_bp Z-spread over the benchmark curve in basis points

Optional JSON fields
--------------------
  pool_seasoning     Months already elapsed since origination (default 0)
  cpr                Flat CPR assumption — annualised (default 0.06 = 6%)
  cdr                Conditional default rate — annualised (default 0 for
                     mbs_agency; 0.02 for others)
  loss_severity      LGD fraction on defaulted balance (default 0.40)
  prepayment_model   cpr | psa (default psa for MBS types, cpr for ABS)
  psa_speed          PSA speed as a percentage (default 100 = 100 PSA)
  credit_support_pct Subordination below this tranche as % of pool balance
                     (default 0)
  senior_notes_balance  Balance of tranches senior to this one; those tranches
                        are paid principal first in the sequential waterfall
                        (default 0 = this is the most senior rated tranche)
  recovery_lag_months   Months between default event and recovery receipt
                        (default 6)
  settlement_days    Days to settlement (default 2)
  calendar           TARGET | UnitedStates (default UnitedStates)
  currency           Settlement currency — used for discount curve selection
  description        Human-readable name
"""

import argparse
import math
from collections import deque
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


# ---------------------------------------------------------------------------
# Prepayment helpers
# ---------------------------------------------------------------------------

def _pmt(monthly_rate, months, balance):
    """Fixed monthly payment for a fully-amortising loan (Excel PMT equivalent)."""
    if months <= 0:
        return float(balance)
    if monthly_rate < 1e-12:
        return float(balance) / months
    return float(balance) * monthly_rate / (1.0 - (1.0 + monthly_rate) ** (-months))


def _cpr_for_month(month, model, flat_cpr, psa_speed, seasoning):
    """Annualised CPR for pool-relative month m (1-indexed), adjusted for seasoning."""
    if model == 'psa':
        aged    = seasoning + month
        cpr_base = min(aged / 30.0, 1.0) * 0.06
        return cpr_base * psa_speed / 100.0
    return flat_cpr


# ---------------------------------------------------------------------------
# Pool cash flow generation
# ---------------------------------------------------------------------------

def _generate_pool_cashflows(bond_data, calendar, settlement_date):
    pool_balance  = float(bond_data['pool_balance'])
    pool_wac      = normalize_rate(bond_data['pool_wac'])
    wam           = int(bond_data['pool_wam'])
    seasoning     = int(bond_data.get('pool_seasoning', 0))
    instrument    = str(bond_data.get('instrument_type', 'abs')).lower()

    default_model = 'psa' if instrument.startswith('mbs') else 'cpr'
    model         = str(bond_data.get('prepayment_model', default_model)).lower()

    flat_cpr       = normalize_rate(bond_data.get('cpr', 0.06))
    psa_speed      = float(bond_data.get('psa_speed', 100.0))

    default_cdr    = 0.0 if instrument == 'mbs_agency' else 0.02
    cdr            = normalize_rate(bond_data.get('cdr', default_cdr))
    loss_severity  = float(bond_data.get('loss_severity', 0.40))
    recovery_lag   = int(bond_data.get('recovery_lag_months', 6))

    monthly_rate   = pool_wac / 12.0
    wam_remaining  = max(1, wam - seasoning)

    B              = pool_balance
    rows           = []
    recovery_queue = deque()   # (pay_month, amount)

    for m in range(1, wam_remaining + 1):
        months_left = wam_remaining - (m - 1)

        # Scheduled payment and interest
        scheduled_payment = _pmt(monthly_rate, months_left, B)
        interest          = B * monthly_rate
        sched_principal   = min(scheduled_payment - interest, B)
        sched_principal   = max(0.0, sched_principal)

        # CPR → SMM
        cpr_m  = _cpr_for_month(m, model, flat_cpr, psa_speed, seasoning)
        smm    = 1.0 - (1.0 - cpr_m) ** (1.0 / 12.0)

        # CDR → MDR → defaults
        mdr      = 1.0 - (1.0 - cdr) ** (1.0 / 12.0)
        defaults = B * mdr
        losses   = defaults * loss_severity

        # Prepayment on remaining balance after scheduled principal
        remaining   = max(0.0, B - sched_principal)
        prepayment  = remaining * smm

        # Queue recovery payment (arrives after lag)
        recovery_queue.append((m + recovery_lag, defaults * (1.0 - loss_severity)))

        # Collect recoveries due this month
        recovery = 0.0
        while recovery_queue and recovery_queue[0][0] <= m:
            recovery += recovery_queue.popleft()[1]

        pool_principal = sched_principal + prepayment + recovery
        pay_date       = calendar.advance(settlement_date, m, ql.Months)

        rows.append({
            'month':            m,
            'date':             pay_date,
            'date_iso':         pay_date.ISO(),
            'opening_balance':  B,
            'interest':         interest,
            'sched_principal':  sched_principal,
            'prepayment':       prepayment,
            'defaults':         defaults,
            'losses':           losses,
            'recovery':         recovery,
            'pool_principal':   pool_principal,
            'cpr':              cpr_m,
        })

        B = max(0.0, B - sched_principal - prepayment - defaults)
        if B < 1e-6:
            break

    return rows


# ---------------------------------------------------------------------------
# Sequential tranche waterfall
# ---------------------------------------------------------------------------

def _apply_tranche_waterfall(pool_rows, bond_data):
    pool_balance_init  = float(bond_data['pool_balance'])
    tranche_balance    = float(bond_data['tranche_balance'])
    tranche_coupon     = normalize_rate(bond_data['tranche_coupon'])
    credit_support     = pool_balance_init * float(bond_data.get('credit_support_pct', 0.0)) / 100.0
    senior_remaining   = float(bond_data.get('senior_notes_balance', 0.0))

    T = tranche_balance      # running tranche balance
    P = pool_balance_init    # running pool balance

    rows = []

    for row in pool_rows:
        if T <= 1e-8:
            break

        losses        = row['losses']
        pool_principal = row['pool_principal']

        # 1. Credit support absorbs losses first
        sub_absorb     = min(losses, credit_support)
        credit_support = max(0.0, credit_support - sub_absorb)
        tranche_loss   = max(0.0, losses - sub_absorb)

        # 2. Senior tranches receive principal first (sequential waterfall)
        if senior_remaining > 0:
            to_senior        = min(pool_principal, senior_remaining)
            senior_remaining = max(0.0, senior_remaining - to_senior)
            avail_principal  = max(0.0, pool_principal - to_senior)
        else:
            avail_principal = pool_principal

        # 3. This tranche receives remaining principal up to its balance
        tranche_principal = min(avail_principal, T)

        # 4. Interest at pass-through rate on opening balance
        tranche_interest = T * tranche_coupon / 12.0

        rows.append({
            'month':                    row['month'],
            'date':                     row['date'],
            'date_iso':                 row['date_iso'],
            'tranche_balance_open':     T,
            'tranche_interest':         tranche_interest,
            'tranche_principal':        tranche_principal,
            'tranche_loss':             tranche_loss,
            'tranche_cf':               tranche_interest + tranche_principal,
            'credit_support_remaining': credit_support,
        })

        T = max(0.0, T - tranche_principal - tranche_loss)
        P = max(0.0, P - row['sched_principal'] - row['prepayment'] - row['defaults'])

    return rows


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
    z_spread = issuer_spread_bp / 10_000.0

    calendar        = get_calendar(bond_data.get('calendar', 'UnitedStates'))
    settlement_days = int(bond_data.get('settlement_days', 2))
    settlement_date = calendar.advance(evaluation_date, settlement_days, ql.Days)

    pool_rows    = _generate_pool_cashflows(bond_data, calendar, settlement_date)
    tranche_rows = _apply_tranche_waterfall(pool_rows, bond_data)

    npv                      = 0.0
    total_principal          = 0.0
    total_weighted_principal = 0.0
    cashflows                = []
    dc_act365                = ql.Actual365Fixed()

    for row in tranche_rows:
        d = row['date']
        if d <= evaluation_date:
            continue
        t  = dc_act365.yearFraction(evaluation_date, d)
        df = curve.discount(d) * math.exp(-z_spread * t)
        pv = row['tranche_cf'] * df
        npv += pv

        total_principal          += row['tranche_principal']
        total_weighted_principal += row['tranche_principal'] * t

        cashflows.append({
            'date':             row['date_iso'],
            'tranche_balance':  row['tranche_balance_open'],
            'interest':         row['tranche_interest'],
            'principal':        row['tranche_principal'],
            'loss':             row['tranche_loss'],
            'cf':               row['tranche_cf'],
            'df':               df,
            'pv':               pv,
        })

    wal             = total_weighted_principal / total_principal if total_principal > 1e-8 else 0.0
    tranche_balance = float(bond_data['tranche_balance'])
    price_pct       = npv / tranche_balance * 100.0 if tranche_balance > 0 else 0.0

    result = {
        'selected_npv':          npv,
        'npv':                   npv,
        'npv_to_maturity':       npv,
        'npv_to_worst_call':     npv,
        'npv_to_first_call':     npv,
        'dirty_price':           npv,
        'clean_price':           npv,
        'accrued':               0.0,
        'wal':                   wal,
        'tranche_balance':       tranche_balance,
        'total_principal_returned': total_principal,
        'issuer_spread_bp':      issuer_spread_bp,
        'evaluation_date':       evaluation_date.ISO(),
        'settlement_date':       settlement_date.ISO(),
        'discount_curve_name':   discount_curve_name,
        'cashflows':             cashflows,
        'price_pct': {
            'pv_note':               price_pct,
            'pv_note_to_maturity':   price_pct,
            'pv_note_to_worst_call': price_pct,
            'clean_price':           price_pct,
        },
    }
    if not _skip_sensitivity:
        result['sensitivity'] = price_sensitivity(bond_data, curve_json)
    return result


# ---------------------------------------------------------------------------
# CLI output
# ---------------------------------------------------------------------------

def print_result(bond_data, result):
    par = float(bond_data.get('tranche_balance', 100.0))
    print(f"{bond_data.get('description', bond_data.get('instrument_id'))} "
          f"({bond_data.get('instrument_id')})")
    print(f"Instrument type:       {bond_data.get('instrument_type')}")
    print(f"Evaluation date:       {result['evaluation_date']}")
    print(f"Settlement date:       {result['settlement_date']}")
    print(f"Discount curve:        {result.get('discount_curve_name', '-')}")
    print(f"Z-spread:              {result['issuer_spread_bp']:.2f} bp")
    print()
    print(f"Pool balance:          {float(bond_data['pool_balance']):,.2f}")
    print(f"Pool WAC:              {float(bond_data['pool_wac']) * 100:.4f}%" if float(bond_data['pool_wac']) < 1 else
          f"Pool WAC:              {float(bond_data['pool_wac']):.4f}%")
    print(f"Pool WAM:              {bond_data['pool_wam']} months")
    print(f"Prepayment model:      {bond_data.get('prepayment_model', 'psa/cpr')}"
          + (f"  ({bond_data.get('psa_speed', 100):.0f} PSA)"
             if bond_data.get('prepayment_model', '') == 'psa' else ''))
    print(f"CDR:                   {float(bond_data.get('cdr', 0.0)) * 100:.2f}%")
    print(f"Loss severity:         {float(bond_data.get('loss_severity', 0.40)) * 100:.1f}%")
    print()
    print(f"Tranche balance:       {par:,.2f}")
    print(f"Tranche coupon:        {float(bond_data['tranche_coupon']) * 100:.4f}%" if float(bond_data['tranche_coupon']) < 1 else
          f"Tranche coupon:        {float(bond_data['tranche_coupon']):.4f}%")
    print(f"Credit support:        {bond_data.get('credit_support_pct', 0.0):.2f}%")
    print()
    print(f"NPV:                   {result['npv']:,.6f}  ({result['npv'] / par * 100:.4f}%)")
    print(f"WAL:                   {result['wal']:.4f} years")
    print(f"Principal returned:    {result['total_principal_returned']:,.2f}")
    sensitivity = result.get('sensitivity')
    if sensitivity:
        base_bp = float(bond_data.get('issuer_spread_bp', bond_data.get('credit_spread_bp', 0.0)))
        print('Sensitivity (NPV %):')
        for s in sensitivity:
            marker = ' ◀' if abs(s['spread_bp'] - base_bp) < 0.01 else ''
            print(f"  {s['spread_bp']:>8.2f} bp  →  {s['pv_note_pct']:.6f}%{marker}")
    print()


def parse_args():
    parser = argparse.ArgumentParser(description='Price ABS / MBS tranches.')
    parser.add_argument('--bond-file',        required=True,           help='Path to instrument JSON')
    parser.add_argument('--curve-file',       default=str(CURVE_FILE), help='Path to curve JSON')
    parser.add_argument('--issuer-spread-bp', type=float, default=None)
    return parser.parse_args()


def main():
    args       = parse_args()
    bond_data  = load_json(Path(args.bond_file))
    curve_json = load_json(Path(args.curve_file))
    result     = price_asset(bond_data, curve_json, issuer_spread_bp=args.issuer_spread_bp)
    print_result(bond_data, result)
    pdf_path = pdf_report.create_pdf_report(
        model_name='abs',
        instrument_id=bond_data.get('instrument_id', 'unknown'),
        input_payload=bond_data,
        output_payload=result,
    )
    print(f'PDF report: {pdf_path}')


if __name__ == '__main__':
    main()
