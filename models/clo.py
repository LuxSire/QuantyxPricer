"""CLO (Collateralised Loan Obligation) tranche pricer.

Prices a single rated CLO tranche through a period-by-period simulation that
models the two-phase structure of a CLO and a simplified OC-test waterfall.
The floating coupon leg is projected using forward rates extracted from the
same benchmark discount curve used by the Hull-White bond pricer (SOFR / Euribor
OIS curve), so the CLO effectively reuses the Hull-White interest-rate model
for all rate projections while adding the pool dynamics on top.

CLO structure modelled
----------------------
Phase 1 – Reinvestment period (evaluation_date → reinvestment_end_date):
  Pool principal cash flows (loan repayments, amortisations) are reinvested by
  the manager; only CDR-driven defaults permanently reduce the pool balance.
  Investors receive ONLY the floating interest leg of their tranche.

Phase 2 – Amortisation period (reinvestment_end_date → maturity_date):
  No further reinvestment. The pool amortises linearly over pool_wal years
  (the expected remaining WAL of the collateral after reinvestment ends) with
  additional CDR prepayments and CDR defaults.
  Principal is returned to tranches sequentially (senior-first).

OC test (simplified)
--------------------
Each payment period:
  oc_ratio = pool_par_balance / total_rated_notes_outstanding

If oc_ratio < oc_threshold and the tranche is NOT the most senior:
  • This tranche's interest payment is deferred (set to zero) until the test
    cures — reflecting the standard interest diversion to pay down seniors.
If oc_ratio < oc_threshold and the tranche IS the most senior (or oc_cure is
  enabled): pool_principal is accelerated to reduce the senior balance and
  restore the OC ratio.

Forward rate projection
-----------------------
  fwd_rate(t1, t2) = (DF(t1) / DF(t2) − 1) / yearFraction(t1, t2)

Tranche coupon each period:
  interest(t) = tranche_balance(t) × [fwd_rate(t, t+dt) + tranche_spread] × dt

Pool interest income each period:
  pool_income(t) = pool_balance(t) × [fwd_rate(t, t+dt) + pool_was] × dt

Credit losses
-------------
  period_default_rate = 1 − (1 − pool_cdr) ^ accrual
  pool_loss(t)        = pool_balance(t) × period_default_rate × loss_severity

  Credit support (equity + junior tranches below this one) absorbs losses first.
  Any loss in excess of credit support reduces the tranche balance.

Instrument types (instrument_type — required)
---------------------------------------------
  clo_tranche       Rated CLO note. Requires pool_par_balance, pool_was,
                    pool_cdr, reinvestment_end_date, tranche_balance,
                    tranche_spread, oc_threshold, credit_support_pct.

  leveraged_loan    Single amortising floating-rate loan (TLB / TLA / unitranche).
                    Uses the same forward-rate projection engine as the CLO but
                    without a pool or waterfall. Requires loan_balance, spread,
                    maturity_date only. Supports a SOFR/Euribor floor and
                    scheduled amortisation (TLB convention: 1% p.a.).

Leveraged loan mechanics
------------------------
  Each period [d0, d1]:
    floored_rate = max(fwd_rate(d0, d1), floor_rate)
    interest(t)  = loan_balance(t) × (floored_rate + spread) × accrual
    amort(t)     = loan_balance × annual_amortisation_pct / 100 / periods_per_year
                   (full bullet for the last period)
    CF(t)        = interest(t) + amort(t)

  DM = issuer_spread_bp (for a floating-rate loan the z-spread equals the
  discount margin when projected and discounted off the same benchmark curve).

Required JSON fields
--------------------
  instrument_id         ISIN or internal identifier
  evaluation_date       Pricing date (DD-MM-YYYY or YYYY-MM-DD)
  instrument_type       clo_tranche | leveraged_loan
  maturity_date         Legal final maturity (DD-MM-YYYY or YYYY-MM-DD)
  credit_spread_bp      Z-spread / DM for discounting in basis points

  For clo_tranche only:
    reinvestment_end_date  End of reinvestment period
    pool_par_balance       Current outstanding pool par balance
    pool_was               Pool weighted average spread (decimal or %)
    pool_cdr               Annual conditional default rate (decimal or %)
    tranche_balance        Outstanding notional of the tranche
    tranche_spread         Spread over the reference rate (decimal or %)
    oc_threshold           OC ratio trigger (e.g. 1.25 = 125%)
    credit_support_pct     Subordination below this tranche as % of pool balance

  For leveraged_loan only:
    loan_balance           Outstanding principal of the loan
    spread                 Spread over the reference rate (decimal or %)

Optional JSON fields
--------------------
  coupon_frequency      Quarterly (default) | Monthly | Semiannual
  settlement_days       Days to settlement (default 2)
  calendar              TARGET | UnitedStates (default TARGET)
  currency              For discount curve selection (e.g. EUR, USD)
  description           Human-readable name

  For clo_tranche only:
    pool_recovery_rate    Recovery on defaulted loans (default 0.65)
    pool_cpr              Annual loan prepayment rate (default 0.15)
    pool_wal              Expected pool WAL post-reinvestment in years (default 3.0)
    equity_pct            Equity tranche as % of pool — used for OC denominator
                          (default 10.0)
    senior_notes_balance  Aggregate balance of senior tranches (default 0)
    tranche_is_senior     True if this is the most senior rated tranche (default False)
    management_fee_bp     Senior management fee in bp p.a. (default 25)

  For leveraged_loan only:
    floor_rate            Reference rate floor — e.g. 0.005 = 50 bp SOFR floor
                          (default 0)
    annual_amortisation_pct  Scheduled principal amortisation per year as % of
                             original balance — 1.0 = TLB convention (default 1.0)
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

_BDC = {
    'ModifiedFollowing': ql.ModifiedFollowing,
    'Following':         ql.Following,
    'Unadjusted':        ql.Unadjusted,
}

_FREQUENCIES = {
    'Monthly':    ql.Monthly,
    'Quarterly':  ql.Quarterly,
    'Semiannual': ql.Semiannual,
}


# ---------------------------------------------------------------------------
# Forward rate
# ---------------------------------------------------------------------------

def _forward_rate(curve, d_start, d_end, day_count):
    """Simple forward rate from the discount curve for [d_start, d_end]."""
    yf = day_count.yearFraction(d_start, d_end)
    if yf <= 1e-10:
        return 0.0
    df0 = curve.discount(d_start)
    df1 = curve.discount(d_end)
    if df1 <= 0:
        return 0.0
    return (df0 / df1 - 1.0) / yf


# ---------------------------------------------------------------------------
# Payment schedule
# ---------------------------------------------------------------------------

def _build_schedule(bond_data, evaluation_date, maturity_date, calendar, bdc):
    freq = _FREQUENCIES.get(bond_data.get('coupon_frequency', 'Quarterly'), ql.Quarterly)
    return ql.Schedule(
        evaluation_date, maturity_date,
        ql.Period(freq),
        calendar, bdc, bdc,
        ql.DateGeneration.Forward, False,
    )


# ---------------------------------------------------------------------------
# Period-by-period CLO simulation
# ---------------------------------------------------------------------------

def _simulate(bond_data, curve, evaluation_date, settlement_date, calendar):
    maturity_date        = parse_date(bond_data['maturity_date'])
    reinvestment_end     = parse_date(bond_data['reinvestment_end_date'])

    pool_balance         = float(bond_data['pool_par_balance'])
    pool_was             = normalize_rate(bond_data['pool_was'])
    pool_cdr             = normalize_rate(bond_data['pool_cdr'])
    pool_cpr             = normalize_rate(bond_data.get('pool_cpr', 0.15))
    pool_wal             = float(bond_data.get('pool_wal', 3.0))
    pool_recovery_rate   = float(bond_data.get('pool_recovery_rate', 0.65))
    loss_severity        = 1.0 - pool_recovery_rate

    tranche_balance      = float(bond_data['tranche_balance'])
    tranche_spread       = normalize_rate(bond_data['tranche_spread'])
    oc_threshold         = float(bond_data.get('oc_threshold', 1.25))
    credit_support_init  = pool_balance * float(bond_data.get('credit_support_pct', 0.0)) / 100.0
    senior_remaining     = float(bond_data.get('senior_notes_balance', 0.0))
    tranche_is_senior    = bool(bond_data.get('tranche_is_senior', False))
    equity_pct           = float(bond_data.get('equity_pct', 10.0))
    mgmt_fee_bp          = float(bond_data.get('management_fee_bp', 25.0))

    bdc     = ql.ModifiedFollowing
    dc_ref  = ql.Actual360()   # standard for SOFR / Euribor projections
    dc_disc = ql.Actual365Fixed()

    schedule = _build_schedule(bond_data, evaluation_date, maturity_date, calendar, bdc)

    # Total rated notes = pool balance × (1 − equity fraction)
    total_rated_notes = pool_balance * (1.0 - equity_pct / 100.0)

    P              = pool_balance
    T              = tranche_balance
    credit_support = credit_support_init
    rows           = []

    for i in range(1, len(schedule)):
        d0 = schedule[i - 1]
        d1 = schedule[i]
        if d1 <= evaluation_date:
            continue
        if T <= 1e-6:
            break

        accrual = dc_ref.yearFraction(d0, d1)
        fwd     = _forward_rate(curve, d0, d1, dc_ref)

        in_reinvestment = d1 <= reinvestment_end

        # --- Pool income ---
        pool_income    = P * (fwd + pool_was) * accrual
        mgmt_fee       = P * (mgmt_fee_bp / 10_000.0) * accrual
        net_pool_income = max(0.0, pool_income - mgmt_fee)

        # --- Pool credit losses ---
        period_dr      = 1.0 - (1.0 - pool_cdr) ** accrual
        pool_defaults  = P * period_dr
        pool_losses    = pool_defaults * loss_severity

        # --- OC test ---
        oc_ratio      = P / total_rated_notes if total_rated_notes > 1e-6 else 999.0
        oc_pass        = oc_ratio >= oc_threshold

        # --- Tranche interest ---
        tranche_interest_full = T * (fwd + tranche_spread) * accrual

        if not oc_pass and not tranche_is_senior:
            # Divert: tranche interest deferred; payment set to zero
            tranche_interest = 0.0
            oc_diversion     = tranche_interest_full   # goes to senior paydown
        else:
            tranche_interest = tranche_interest_full
            oc_diversion     = 0.0

        # --- Principal ---
        if in_reinvestment:
            # Pool reinvests all principal; manager keeps pool balance flat net of losses
            pool_principal    = 0.0
            tranche_principal = 0.0
            # Pool balance: CDR defaults reduce it (recovery is reinvested too)
            P = max(0.0, P - pool_defaults)
        else:
            # Linear amortisation of remaining pool balance over pool_wal
            # plus voluntary repayments (CPR-like)
            periods_elapsed  = dc_ref.yearFraction(reinvestment_end, d1)
            linear_rate      = accrual / max(pool_wal, accrual)
            scheduled_pool_P = P * linear_rate
            voluntary_pool_P = P * pool_cpr * accrual
            pool_recovery    = pool_defaults * pool_recovery_rate
            pool_principal   = scheduled_pool_P + voluntary_pool_P + pool_recovery

            # OC diversion also contributes to senior paydown
            pool_principal  += oc_diversion / max(pool_balance, 1.0) * P  # scale proportionally

            # Senior tranches first
            if senior_remaining > 0:
                to_senior        = min(pool_principal, senior_remaining)
                senior_remaining = max(0.0, senior_remaining - to_senior)
                avail            = max(0.0, pool_principal - to_senior)
            else:
                avail = pool_principal

            tranche_principal = min(avail, T)

            P = max(0.0, P - scheduled_pool_P - voluntary_pool_P - pool_defaults)

        # --- Credit losses waterfall ---
        sub_absorb     = min(pool_losses, credit_support)
        credit_support = max(0.0, credit_support - sub_absorb)
        tranche_loss   = max(0.0, pool_losses - sub_absorb)

        T              = max(0.0, T - tranche_principal - tranche_loss)
        total_rated_notes = max(0.0, total_rated_notes - tranche_loss)

        tranche_cf = tranche_interest + tranche_principal

        rows.append({
            'date_start':              d0.ISO(),
            'date_end':                d1.ISO(),
            'date':                    d1,
            'accrual':                 accrual,
            'fwd_rate':                fwd,
            'pool_balance':            P + (scheduled_pool_P if not in_reinvestment else 0.0),
            'pool_income':             pool_income,
            'pool_losses':             pool_losses,
            'oc_ratio':                oc_ratio,
            'oc_pass':                 oc_pass,
            'tranche_balance_open':    T + tranche_principal + tranche_loss,
            'tranche_interest':        tranche_interest,
            'tranche_principal':       tranche_principal,
            'tranche_loss':            tranche_loss,
            'tranche_cf':              tranche_cf,
            'credit_support':          credit_support,
            'in_reinvestment':         in_reinvestment,
        })

    return rows


# ---------------------------------------------------------------------------
# Leveraged loan cash flow generator
# ---------------------------------------------------------------------------

def _price_leveraged_loan(bond_data, curve, evaluation_date, calendar):
    """Generate periodic cash flows for a single amortising floating-rate loan."""
    maturity_date = parse_date(bond_data['maturity_date'])
    loan_balance  = float(bond_data['loan_balance'])
    spread        = normalize_rate(bond_data['spread'])
    floor_rate    = normalize_rate(bond_data.get('floor_rate', 0.0))
    annual_amort  = float(bond_data.get('annual_amortisation_pct', 1.0)) / 100.0

    bdc    = ql.ModifiedFollowing
    dc_ref = ql.Actual360()

    schedule = _build_schedule(bond_data, evaluation_date, maturity_date, calendar, bdc)

    freq_label       = bond_data.get('coupon_frequency', 'Quarterly')
    periods_per_year = {'Monthly': 12, 'Quarterly': 4, 'Semiannual': 2}.get(freq_label, 4)
    amort_per_period = loan_balance * annual_amort / periods_per_year

    L    = loan_balance
    rows = []

    for i in range(1, len(schedule)):
        d0 = schedule[i - 1]
        d1 = schedule[i]
        if d1 <= evaluation_date:
            continue
        if L <= 1e-6:
            break

        accrual      = dc_ref.yearFraction(d0, d1)
        fwd          = _forward_rate(curve, d0, d1, dc_ref)
        floored_rate = max(fwd, floor_rate)
        interest     = L * (floored_rate + spread) * accrual

        is_last   = (i == len(schedule) - 1)
        principal = L if is_last else min(amort_per_period, L)

        rows.append({
            'date_start':           d0.ISO(),
            'date_end':             d1.ISO(),
            'date':                 d1,
            'accrual':              accrual,
            'fwd_rate':             fwd,
            'floored_rate':         floored_rate,
            'tranche_balance_open': L,
            'tranche_interest':     interest,
            'tranche_principal':    principal,
            'tranche_loss':         0.0,
            'tranche_cf':           interest + principal,
            'oc_ratio':             None,
            'oc_pass':              True,
            'in_reinvestment':      False,
        })

        L = max(0.0, L - principal)

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

    calendar        = get_calendar(bond_data.get('calendar', 'TARGET'))
    settlement_days = int(bond_data.get('settlement_days', 2))
    settlement_date = calendar.advance(evaluation_date, settlement_days, ql.Days)

    instrument_type = str(bond_data.get('instrument_type', 'clo_tranche')).lower()

    if instrument_type == 'leveraged_loan':
        rows = _price_leveraged_loan(bond_data, curve, evaluation_date, calendar)
        face = float(bond_data['loan_balance'])
    else:
        rows = _simulate(bond_data, curve, evaluation_date, settlement_date, calendar)
        face = float(bond_data['tranche_balance'])

    npv                      = 0.0
    total_principal          = 0.0
    total_weighted_principal = 0.0
    cashflows                = []
    dc_disc                  = ql.Actual365Fixed()
    oc_failures              = 0

    for row in rows:
        d  = row['date']
        t  = dc_disc.yearFraction(evaluation_date, d)
        df = curve.discount(d) * math.exp(-z_spread * t)
        pv = row['tranche_cf'] * df
        npv += pv

        total_principal          += row['tranche_principal']
        total_weighted_principal += row['tranche_principal'] * t

        if not row['oc_pass']:
            oc_failures += 1

        cf_entry = {
            'date_start':      row['date_start'],
            'date':            row['date_end'],
            'fwd_rate':        row['fwd_rate'],
            'tranche_balance': row['tranche_balance_open'],
            'interest':        row['tranche_interest'],
            'principal':       row['tranche_principal'],
            'loss':            row['tranche_loss'],
            'cf':              row['tranche_cf'],
            'oc_ratio':        row['oc_ratio'],
            'oc_pass':         row['oc_pass'],
            'in_reinvestment': row['in_reinvestment'],
            'df':              df,
            'pv':              pv,
        }
        if instrument_type == 'leveraged_loan':
            cf_entry['floored_rate'] = row['floored_rate']
        cashflows.append(cf_entry)

    wal       = total_weighted_principal / total_principal if total_principal > 1e-8 else 0.0
    price_pct = npv / face * 100.0 if face > 0 else 0.0

    result = {
        'selected_npv':             npv,
        'npv':                      npv,
        'npv_to_maturity':          npv,
        'npv_to_worst_call':        npv,
        'npv_to_first_call':        npv,
        'dirty_price':              npv,
        'clean_price':              npv,
        'accrued':                  0.0,
        'wal':                      wal,
        'instrument_type':          instrument_type,
        'total_principal_returned': total_principal,
        'issuer_spread_bp':         issuer_spread_bp,
        'evaluation_date':          evaluation_date.ISO(),
        'settlement_date':          settlement_date.ISO(),
        'discount_curve_name':      discount_curve_name,
        'cashflows':                cashflows,
        'price_pct': {
            'pv_note':               price_pct,
            'pv_note_to_maturity':   price_pct,
            'pv_note_to_worst_call': price_pct,
            'clean_price':           price_pct,
        },
    }

    if instrument_type == 'leveraged_loan':
        result['loan_balance'] = face
        result['dm_bp']        = issuer_spread_bp
    else:
        result['tranche_balance']  = face
        result['oc_test_failures'] = oc_failures

    if not _skip_sensitivity:
        result['sensitivity'] = price_sensitivity(bond_data, curve_json)
    return result


# ---------------------------------------------------------------------------
# CLI output
# ---------------------------------------------------------------------------

def print_result(bond_data, result):
    if result.get('instrument_type') == 'leveraged_loan':
        _print_loan(bond_data, result)
    else:
        _print_clo(bond_data, result)


def _print_loan(bond_data, result):
    par   = float(bond_data.get('loan_balance', 100.0))
    sprd  = normalize_rate(bond_data['spread'])
    floor = normalize_rate(bond_data.get('floor_rate', 0.0))
    print(f"{bond_data.get('description', bond_data.get('instrument_id'))} "
          f"({bond_data.get('instrument_id')})")
    print(f"Model:                 Leveraged loan (floating-rate, amortising)")
    print(f"Evaluation date:       {result['evaluation_date']}")
    print(f"Settlement date:       {result['settlement_date']}")
    print(f"Maturity:              {bond_data.get('maturity_date')}")
    print(f"Discount curve:        {result.get('discount_curve_name', '-')}")
    print()
    print(f"Loan balance:          {par:,.2f}")
    print(f"Spread:                S+{sprd * 10_000:.0f} bp  ({sprd * 100:.2f}%)")
    if floor > 0:
        print(f"Floor:                 {floor * 100:.3f}%")
    print(f"Annual amortisation:   {bond_data.get('annual_amortisation_pct', 1.0):.2f}%")
    print(f"DM (z-spread):         {result['issuer_spread_bp']:.2f} bp")
    print()
    print(f"NPV:                   {result['npv']:,.6f}  ({result['npv'] / par * 100:.4f}%)")
    print(f"WAL:                   {result['wal']:.4f} years")
    print()
    print('Period detail:')
    for cf in result['cashflows']:
        floored = cf.get('floored_rate', cf['fwd_rate'])
        flag    = '*' if floored > cf['fwd_rate'] else ' '
        print(f"  {cf['date']}  fwd={cf['fwd_rate'] * 100:.3f}%{flag}"
              f"  bal={cf['tranche_balance']:,.2f}"
              f"  int={cf['interest']:,.2f}  prin={cf['principal']:,.2f}"
              f"  pv={cf['pv']:,.4f}")
    if any(cf.get('floored_rate', cf['fwd_rate']) > cf['fwd_rate'] for cf in result['cashflows']):
        print('  * floor active')
    print()


def _print_clo(bond_data, result):
    par = float(bond_data.get('tranche_balance', 100.0))
    print(f"{bond_data.get('description', bond_data.get('instrument_id'))} "
          f"({bond_data.get('instrument_id')})")
    print(f"Model:                 CLO tranche (floating-rate, pool simulation)")
    print(f"Evaluation date:       {result['evaluation_date']}")
    print(f"Settlement date:       {result['settlement_date']}")
    print(f"Reinvestment ends:     {bond_data.get('reinvestment_end_date')}")
    print(f"Maturity:              {bond_data.get('maturity_date')}")
    print(f"Discount curve:        {result.get('discount_curve_name', '-')}")
    print(f"Z-spread:              {result['issuer_spread_bp']:.2f} bp")
    print()
    print(f"Pool par balance:      {float(bond_data['pool_par_balance']):,.2f}")
    print(f"Pool WAS:              {normalize_rate(bond_data['pool_was']) * 100:.2f}%")
    print(f"Pool CDR:              {normalize_rate(bond_data['pool_cdr']) * 100:.2f}%")
    print(f"Pool WAL (post-reinv): {bond_data.get('pool_wal', 3.0)} years")
    print(f"Recovery rate:         {float(bond_data.get('pool_recovery_rate', 0.65)) * 100:.1f}%")
    print()
    print(f"Tranche balance:       {par:,.2f}")
    print(f"Tranche spread:        {normalize_rate(bond_data['tranche_spread']) * 100:.2f}%")
    print(f"Credit support:        {bond_data.get('credit_support_pct', 0.0):.2f}%")
    print(f"OC threshold:          {float(bond_data.get('oc_threshold', 1.25)):.2f}x")
    print()
    print(f"NPV:                   {result['npv']:,.6f}  ({result['npv'] / par * 100:.4f}%)")
    print(f"WAL:                   {result['wal']:.4f} years")
    print(f"Principal returned:    {result['total_principal_returned']:,.2f}")
    if result.get('oc_test_failures'):
        print(f"OC test failures:      {result['oc_test_failures']} period(s)")
    sensitivity = result.get('sensitivity')
    if sensitivity:
        base_bp = float(bond_data.get('issuer_spread_bp', bond_data.get('credit_spread_bp', 0.0)))
        print('Sensitivity (NPV %):')
        for s in sensitivity:
            marker = ' ◀' if abs(s['spread_bp'] - base_bp) < 0.01 else ''
            print(f"  {s['spread_bp']:>8.2f} bp  →  {s['pv_note_pct']:.6f}%{marker}")
    print()
    print('Period detail:')
    for cf in result['cashflows']:
        phase = 'REINV' if cf['in_reinvestment'] else 'AMORT'
        oc    = 'PASS' if cf['oc_pass'] else 'FAIL'
        print(f"  {cf['date']}  [{phase}] [OC {oc} {cf['oc_ratio']:.2f}x]"
              f"  fwd={cf['fwd_rate'] * 100:.3f}%"
              f"  int={cf['interest']:,.2f}  prin={cf['principal']:,.2f}"
              f"  pv={cf['pv']:,.4f}")
    print()


def parse_args():
    parser = argparse.ArgumentParser(description='Price CLO tranches.')
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
        model_name='clo',
        instrument_id=bond_data.get('instrument_id', 'unknown'),
        input_payload=bond_data,
        output_payload=result,
    )
    print(f'PDF report: {pdf_path}')


if __name__ == '__main__':
    main()
