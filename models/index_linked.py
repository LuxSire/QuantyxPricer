"""Inflation-linked structured channel note pricer.

Prices structured notes where both coupons and redemption are scaled by a
forward-projected inflation index ratio.  The common QuantLib utilities
(curve building, schedule generation, z-spread discounting, inflation
projection) are imported from models/helper.py — the same shared layer
used by the structured note pricers.  Neither module depends on the other.

For plain-vanilla government linkers (TIPS, UK Gilts, OATi, BTP-i) use
inflation_linked.py instead.

Index ratio projection
----------------------
  IndexRatio(d) = index_ratio_at_eval × (1 + annual_index_growth_rate) ^ yearFraction(eval, d)

Required JSON fields
--------------------
  instrument_id            ISIN or internal identifier
  evaluation_date          Pricing date (DD-MM-YYYY or YYYY-MM-DD)
  coupon_structure         Must be 'index_linked'
  note_notional            Notional of the note (e.g. 100000000)
  coupon_frequency         Annual | Semiannual | Quarterly | Monthly
  accrual_day_count        Day count convention
  calendar                 TARGET | UnitedStates
  business_day_convention  ModifiedFollowing | Following | Unadjusted
  collateral               Collateral bond object (same schema as models/helper.model_collateral_pv)

  index_linked_assumption  Object with:
    index_ratio_at_eval          Current index ratio at evaluation date
    annual_index_growth_rate     Forward flat inflation rate (decimal or %)
    coupon_multiplier            Real coupon rate / multiplier applied to each coupon

Optional JSON fields
--------------------
  index_linked_terms         Object declaring contractual term fields
  missing_contractual_terms  List of terms acknowledged as contractually missing
  credit_spread_bp           Issuer spread on the note (default 0)
  collateral_spread_bp       Additional spread on the collateral leg (default 0)
  issuer_call                Set to 'Applicable' to enable a call schedule
  call_dates                 List of call dates (DD-MM-YYYY)
"""

import argparse
import math
from pathlib import Path

import QuantLib as ql

from .helper import (
    parse_date,
    get_day_count,
    load_json,
    build_discount_curve_and_dc,
    build_note_dates,
    discount_factor_with_issuer_spread,
    inflation_factor,
    select_note_curve,
    select_collateral_curve,
    model_collateral_pv,
    compute_valuation_adjustments,
    ASSETS_DIR,
    CURVES_DIR,
)

try:
    from reporting import pdf_report
except ModuleNotFoundError:
    import reporting.pdf_report as pdf_report


CURVE_FILE = CURVES_DIR / 'swap_curves.json'
BOND_FILE  = ASSETS_DIR / 'XS0316010023.json'

REQUIRED_CONTRACT_FIELDS = [
    'underlying_name',
    'reference_index_name',
    'initial_reference_level',
    'current_reference_level',
    'pricing_formula_type',
    'coupon_formula_type',
    'redemption_formula_type',
    'principal_protection_pct',
    'observation_dates',
    'issuer_call_rights',
]


def assess_contract_completeness(note_data):
    terms   = dict(note_data.get('index_linked_terms', {}))
    missing = []
    for field in REQUIRED_CONTRACT_FIELDS:
        value = terms.get(field)
        if value is None:
            missing.append(field)
        elif isinstance(value, str) and not value.strip():
            missing.append(field)
        elif isinstance(value, list) and not value:
            missing.append(field)

    for field in note_data.get('missing_contractual_terms', []):
        if field not in missing:
            missing.append(field)

    return {'is_complete': not missing, 'missing_fields': missing, 'terms': terms}


def get_index_assumption(note_data):
    assumption = dict(note_data.get('index_linked_assumption', {}))
    if not assumption:
        assumption = dict(note_data.get('collateral', {}).get('inflation_assumption', {}))
    return {
        'index_ratio_at_eval':  float(assumption.get('index_ratio_at_eval', 1.0)),
        'annual_inflation_rate': float(
            assumption.get('annual_index_growth_rate',
            assumption.get('annual_inflation_rate', 0.0))
        ),
        'coupon_multiplier': float(
            assumption.get('coupon_multiplier', note_data.get('fixed_coupon_rate', 0.0))
        ),
    }


def _solve_ytm(cashflows, evaluation_date, current_pv):
    if current_pv <= 0 or not cashflows:
        return 0.0
    day_count = ql.Actual365Fixed()

    def pv_at(ytm):
        total = 0.0
        for cf in cashflows:
            iso  = cf['date']
            y, m, d = map(int, iso.split('-'))
            t    = day_count.yearFraction(evaluation_date, ql.Date(d, m, y))
            if t > 0:
                total += cf['amount'] * math.exp(-ytm * t)
        return total

    ytm = 0.03
    for _ in range(100):
        npv = pv_at(ytm) - current_pv
        if abs(npv) < 1e-6:
            break
        eps  = 1e-8
        deriv = (pv_at(ytm + eps) - npv - current_pv + current_pv) / eps
        # simplified: derivative of pv_at
        deriv = (pv_at(ytm + eps) - pv_at(ytm)) / eps
        if abs(deriv) < 1e-10:
            break
        ytm -= npv / deriv
        ytm  = max(-0.5, min(1.0, ytm))
    return ytm


def price_note(note_data, curve, curve_day_count):
    """Price the index-linked note leg (coupons + redemption)."""
    eval_date       = ql.Settings.instance().evaluationDate
    note_day_count  = get_day_count(note_data.get('accrual_day_count', 'Actual365Fixed'))
    coupon_structure = note_data.get('coupon_structure', 'index_linked')
    if coupon_structure != 'index_linked':
        raise ValueError(
            f'index_linked pricer requires coupon_structure="index_linked", '
            f'got "{coupon_structure}" for {note_data.get("instrument_id", "unknown")}.'
        )

    notional       = float(note_data.get('note_notional', 100_000_000.0))
    spread_bp      = float(note_data.get('credit_spread_bp', 0.0)) + float(
        note_data.get('collateral_spread_bp') or note_data.get('collateral_spread') or 0.0
    )
    index_assump   = get_index_assumption(note_data)
    dates          = build_note_dates(note_data)

    pv_coupons    = 0.0
    pv_redemption = 0.0
    cashflows     = []

    for i in range(1, len(dates)):
        d0 = dates[i - 1]
        d1 = dates[i]
        if d1 <= eval_date:
            continue
        accrual    = note_day_count.yearFraction(d0, d1)
        idx_ratio  = inflation_factor(eval_date, d1, index_assump)
        coupon_cf  = notional * index_assump['coupon_multiplier'] * accrual * idx_ratio
        df         = discount_factor_with_issuer_spread(curve, curve_day_count, eval_date, d1, spread_bp)
        pv         = coupon_cf * df
        pv_coupons += pv
        cashflows.append({'date': d1.ISO(), 'type': 'coupon', 'amount': coupon_cf, 'df': df, 'pv': pv})

    maturity_date = dates[-1]
    if maturity_date > eval_date:
        idx_ratio_mat = inflation_factor(eval_date, maturity_date, index_assump)
        redemption_cf = notional * idx_ratio_mat
        df_mat        = discount_factor_with_issuer_spread(curve, curve_day_count, eval_date, maturity_date, spread_bp)
        pv_redemption = redemption_cf * df_mat
        cashflows.append({'date': maturity_date.ISO(), 'type': 'redemption',
                          'amount': redemption_cf, 'df': df_mat, 'pv': pv_redemption})

    return {
        'pv_note':           pv_coupons + pv_redemption,
        'pv_note_coupons':   pv_coupons,
        'pv_note_redemption': pv_redemption,
        'cashflows':         cashflows,
        'index_assumption':  index_assump,
    }


def price_asset(note_data, curve_json):
    evaluation_date = parse_date(note_data['evaluation_date'])

    note_curve_cfg,       note_curve_name       = select_note_curve(note_data, curve_json)
    collateral_curve_cfg, collateral_curve_name = select_collateral_curve(note_data, curve_json)
    note_curve,       note_curve_day_count       = build_discount_curve_and_dc(note_curve_cfg,       evaluation_date)
    collateral_curve, collateral_curve_day_count = build_discount_curve_and_dc(collateral_curve_cfg, evaluation_date)

    note_notional = float(note_data.get('note_notional', 100_000_000.0))
    issue_price   = float(note_data.get('issue_price', 100.0))

    note_leg = price_note(note_data, note_curve, note_curve_day_count)

    if note_data.get('collateral'):
        collateral_leg = model_collateral_pv(
            note_data['collateral'], collateral_curve, collateral_curve_day_count
        )
    else:
        collateral_leg = {
            'pv_collateral': 0.0, 'pv_collateral_model': 0.0,
            'valuation_method': None, 'cashflows': [],
        }

    adjustments          = compute_valuation_adjustments(note_data, note_curve, note_curve_day_count)
    contract_completeness = assess_contract_completeness(note_data)

    swap_mode = note_data.get('swap', {}).get('mode', 'calibration_residual')
    if swap_mode == 'calibration_residual':
        pv_swap = note_leg['pv_note'] - collateral_leg['pv_collateral'] + adjustments['pv_total_adjustments']
    else:
        raise ValueError(f'Unsupported swap mode: {swap_mode}')

    lhs = note_leg['pv_note']
    rhs = collateral_leg['pv_collateral'] + pv_swap - adjustments['pv_total_adjustments']
    s   = 100.0 / note_notional
    ytm = _solve_ytm(note_leg['cashflows'], evaluation_date, lhs)

    return {
        'evaluation_date':              evaluation_date.ISO(),
        'note_discount_curve_name':     note_curve_name,
        'collateral_discount_curve_name': collateral_curve_name,
        'issue_price':                  issue_price,
        'note_notional':                note_notional,
        'pv_note':                      lhs * s,
        'selected_npv':                 lhs * s,
        'npv_to_maturity':              lhs * s,
        'npv_to_first_call':            lhs * s,
        'npv_to_worst_call':            lhs * s,
        'pv_collateral':                collateral_leg['pv_collateral'],
        'pv_collateral_model':          collateral_leg['pv_collateral_model'],
        'collateral_valuation_method':  collateral_leg['valuation_method'],
        'pv_swap':                      pv_swap,
        'pv_adjustments':               adjustments,
        'identity_lhs_pv_note':         lhs,
        'identity_rhs_reconstructed':   rhs,
        'identity_error':               lhs - rhs,
        'contract_completeness':        contract_completeness,
        'yield_to_maturity':            ytm,
        'price_pct': {
            'pv_note':                   lhs * s,
            'pv_note_coupons':           note_leg['pv_note_coupons']   * s,
            'pv_note_redemption':        note_leg['pv_note_redemption'] * s,
            'pv_collateral':             collateral_leg['pv_collateral']       * s,
            'pv_collateral_model':       collateral_leg['pv_collateral_model'] * s,
            'pv_swap':                   pv_swap                               * s,
            'pv_fees':                   adjustments['pv_fees']                * s,
            'pv_funding':                adjustments['pv_funding']             * s,
            'pv_csa':                    adjustments['pv_csa']                 * s,
            'pv_residual_basis':         adjustments['pv_residual_basis']      * s,
            'pv_total_adjustments':      adjustments['pv_total_adjustments']   * s,
            'identity_lhs_pv_note':      lhs * s,
            'identity_rhs_reconstructed': rhs * s,
            'identity_error':            (lhs - rhs) * s,
        },
        'note_leg':      note_leg,
        'collateral_leg': collateral_leg,
        'swap_mode':     swap_mode,
    }


def print_report(note_data, result):
    pct        = result['price_pct']
    assumption = result['note_leg']['index_assumption']
    completeness = result['contract_completeness']
    print(f"{note_data.get('description', '')} ({note_data['instrument_id']})")
    print(f"Evaluation date: {result['evaluation_date']}")
    print(f"Note discount curve: {result['note_discount_curve_name']}")
    print(f"Collateral discount curve: {result['collateral_discount_curve_name']}")
    print(f"Issue price (%): {result['issue_price']:.4f}")
    print(f"Index ratio at eval: {assumption['index_ratio_at_eval']:.6f}")
    print(f"Annual index growth assumption: {assumption['annual_inflation_rate']:.6f}")
    print(f"Index coupon multiplier: {assumption['coupon_multiplier']:.6f}")
    print(f"Contract terms complete: {completeness['is_complete']}")
    if completeness['missing_fields']:
        print(f"Missing contractual fields: {', '.join(completeness['missing_fields'])}")
    print(f"PV(Note) %: {pct['pv_note']:.6f}")
    print(f"  - Coupons %: {pct['pv_note_coupons']:.6f}")
    print(f"  - Redemption %: {pct['pv_note_redemption']:.6f}")
    print(f"Yield to Maturity: {result['yield_to_maturity']:.6%}")
    print(f"PV(Collateral) %: {pct['pv_collateral']:.6f}")
    print(f"PV(Collateral model estimate) %: {pct['pv_collateral_model']:.6f}")
    print(f"Collateral valuation method: {result['collateral_valuation_method']}")
    print(f"PV(Swap) %: {pct['pv_swap']:.6f}")
    print(f"PV(Fees) %: {pct['pv_fees']:.6f}")
    print(f"PV(Funding) %: {pct['pv_funding']:.6f}")
    print(f"PV(CSA) %: {pct['pv_csa']:.6f}")
    print(f"PV(Residual Basis) %: {pct['pv_residual_basis']:.6f}")
    print(f"PV(Adjustments Total) %: {pct['pv_total_adjustments']:.6f}")
    print(f"Check LHS PV(Note) %: {pct['identity_lhs_pv_note']:.6f}")
    print(f"Check RHS Collateral+Swap-Adjustments %: {pct['identity_rhs_reconstructed']:.6f}")
    print(f"Identity error %: {pct['identity_error']:.8f}")


# Keep public alias so pricer.py can call index_linked.print_result
print_result = print_report


def parse_args():
    parser = argparse.ArgumentParser(description='Price index-linked channel notes.')
    parser.add_argument('--bond-file',  default=str(BOND_FILE),  help='Path to bond JSON')
    parser.add_argument('--curve-file', default=str(CURVE_FILE), help='Path to swap curve JSON')
    return parser.parse_args()


if __name__ == '__main__':
    args       = parse_args()
    note_data  = load_json(Path(args.bond_file))
    curve_json = load_json(Path(args.curve_file))
    result     = price_asset(note_data, curve_json)
    print_report(note_data, result)
    pdf_path = pdf_report.create_pdf_report(
        model_name='index_linked',
        instrument_id=note_data.get('instrument_id', 'unknown'),
        input_payload=note_data,
        output_payload=result,
    )
    print(f'PDF report: {pdf_path}')
