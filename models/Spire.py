"""Structured note pricer using the Spire collateral + swap framework.

Prices capital-protected and partial-protection structured notes constructed as:

  Note PV = Collateral PV + Swap adjustments − Issuer spread PV

Supports fixed, autocallable, and Bermudan-callable structures.  For inflation-linked
structured channel notes use index_linked.py; for plain-vanilla government linkers use
inflation_linked.py.

Required JSON fields
--------------------
  instrument_id            ISIN or internal identifier
  evaluation_date          Pricing date (DD-MM-YYYY or YYYY-MM-DD)
  coupon_structure         Must be 'fixed' (index_linked has its own module)
  note_notional            Notional of the note (e.g. 100000000)
  coupon_frequency         Annual | Semiannual | Quarterly | Monthly
  accrual_day_count        Day count convention
  calendar                 TARGET | UnitedStates
  business_day_convention  ModifiedFollowing | Following | Unadjusted

  collateral               Object describing the underlying collateral bond:
    principal_amount / principal   Principal of the collateral bond
    coupon_rate                    Fixed coupon rate of the collateral
    issue_date                     DD-MM-YYYY or YYYY-MM-DD
    maturity_date                  DD-MM-YYYY or YYYY-MM-DD
    coupon_frequency               Annual | Semiannual etc.
    calendar                       TARGET | UnitedStates
    business_day_convention        ModifiedFollowing | Following | Unadjusted
    day_count                      Day count convention
    discount_curve_name            Curve to discount collateral cash flows

Optional JSON fields
--------------------
  credit_spread_bp           Issuer spread on the note (default 0)
  collateral_spread_bp       Additional spread applied to the collateral leg (default 0)
  issue_price                Issue price as % of note_notional (default 100)
  redemption                 Final redemption amount or pct (default par)
  issuer_call                Set to 'Applicable' to enable a call schedule
  call_dates                 List of call dates (DD-MM-YYYY)
  callable_type              bermudan | autocallable
  autocall_trigger           Dict with trigger_level_pct, monte_carlo_paths, monte_carlo_vol
  valuation_mode             to_maturity | to_first_call | to_worst_call (default to_maturity)
"""

import argparse
import math
import random
from pathlib import Path

import QuantLib as ql

from . import helper
from .helper import (
    parse_date,
    get_day_count,
    get_calendar,
    load_json,
    build_discount_curve,
    build_discount_curve_and_dc,
    build_note_dates,
    build_regular_schedule,
    discount_factor_with_issuer_spread,
    inflation_factor,
    select_note_curve,
    select_collateral_curve,
    model_collateral_pv,
    spread_cost_from_schedule,
    compute_valuation_adjustments,
)
from . import hullwhite

try:
    from reporting import pdf_report
except ModuleNotFoundError:
    import reporting.pdf_report as pdf_report


BASE_DIR     = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent
ASSETS_DIR   = PROJECT_ROOT / 'assets'
CURVES_DIR   = PROJECT_ROOT / 'curves'
CURVE_FILE   = CURVES_DIR / 'swap_curves.json'
BOND_FILE    = ASSETS_DIR / 'XS2725067362.json'


def _price_with_curve(note_data, curve, curve_day_count,
                      collateral_curve=None, collateral_curve_day_count=None):
    eval_date        = ql.Settings.instance().evaluationDate
    note_day_count   = get_day_count(note_data.get('accrual_day_count', '30/360'))
    coupon_structure = note_data.get('coupon_structure', 'fixed')

    if coupon_structure not in {'fixed', 'zero_coupon'}:
        raise ValueError(
            'Spire supports coupon_structure="fixed" or "zero_coupon" only. '
            f'Received coupon_structure="{coupon_structure}" for '
            f'{note_data.get("instrument_id", "unknown")}. '
            'Use models/hullwhite.py for CMS/floating structures.'
        )

    notional     = float(note_data.get('note_notional', 100_000_000.0))
    coupon_rate  = float(note_data['fixed_coupon_rate'])
    spread_bp    = float(note_data.get('credit_spread_bp', 0.0)) + float(
        note_data.get('collateral_spread_bp') or note_data.get('collateral_spread') or 0.0
    )

    dates         = build_note_dates(note_data)
    maturity_date = dates[-1]

    def pv_to_horizon(horizon_date, redemption_pct):
        pv_coupons    = 0.0
        pv_redemption = 0.0
        cashflows     = []
        for i in range(1, len(dates)):
            d0 = dates[i - 1]
            d1 = dates[i]
            if d1 > horizon_date:
                break
            if d1 <= eval_date:
                continue
            accrual   = note_day_count.yearFraction(d0, d1)
            coupon_cf = notional * coupon_rate * accrual
            df        = discount_factor_with_issuer_spread(curve, curve_day_count, eval_date, d1, spread_bp)
            pv        = coupon_cf * df
            pv_coupons += pv
            cashflows.append({'date': d1.ISO(), 'type': 'coupon', 'amount': coupon_cf, 'df': df, 'pv': pv})

        if horizon_date > eval_date:
            redemption_cf = notional * redemption_pct / 100.0
            df_h          = discount_factor_with_issuer_spread(curve, curve_day_count, eval_date, horizon_date, spread_bp)
            pv_redemption = redemption_cf * df_h
            cashflows.append({'date': horizon_date.ISO(), 'type': 'redemption',
                              'amount': redemption_cf, 'df': df_h, 'pv': pv_redemption})
        return {
            'horizon_date':      horizon_date,
            'pv_note':           pv_coupons + pv_redemption,
            'pv_note_coupons':   pv_coupons,
            'pv_note_redemption': pv_redemption,
            'cashflows':         cashflows,
        }

    raw_call_dates          = note_data.get('call_dates', [])
    issuer_call_applicable  = str(note_data.get('issuer_call', '')).strip().lower() == 'applicable'
    eligible_call_dates     = []
    if issuer_call_applicable and raw_call_dates:
        eligible_call_dates = sorted(
            d for d in [parse_date(x) for x in raw_call_dates]
            if eval_date <= d < maturity_date
        )

    callable_type    = (note_data.get('callable_type') or '').strip().lower()
    autocall_trigger = note_data.get('autocall_trigger', {}) or {}

    def estimate_forward_dirty_price(collateral_data, col_curve, col_dc, call_date):
        issue  = parse_date(collateral_data['issue_date'])
        mat    = parse_date(collateral_data['maturity_date'])
        prin   = float(collateral_data.get('principal_amount', collateral_data.get('principal', 0.0)))
        sched  = build_regular_schedule(
            issue, mat,
            collateral_data.get('coupon_frequency', 'Semiannual'),
            collateral_data.get('calendar', 'TARGET'),
            collateral_data.get('business_day_convention', 'Following'),
        )
        dc         = get_day_count(collateral_data.get('day_count', 'ActualActual'))
        infl_assmp = collateral_data.get('inflation_assumption', {})
        df_call    = col_curve.discount(call_date)
        sum_fwd    = 0.0
        for i in range(1, len(sched)):
            d1 = sched[i]
            if d1 < call_date:
                continue
            accrual   = dc.yearFraction(sched[i - 1], d1)
            idx       = inflation_factor(eval_date, d1, infl_assmp)
            c_rate    = float(collateral_data.get('coupon_rate', 0.0))
            cf        = prin * c_rate * accrual * idx
            if d1 == mat:
                cf += prin * idx
            df_t = col_curve.discount(d1)
            if df_call > 0:
                sum_fwd += cf * (df_t / df_call)
        return 100.0 * (sum_fwd / prin) if prin > 0 else 0.0

    def monte_carlo_call_probability(mean_pct, vol, call_date, trigger_level_pct, paths=1000):
        if mean_pct is None:
            return 0.0
        T = ql.Actual365Fixed().yearFraction(eval_date, call_date)
        if T <= 0:
            return 1.0 if mean_pct >= (trigger_level_pct or 0.0) else 0.0
        mean_dec = max(mean_pct / 100.0, 1e-9)
        sigma    = float(vol) * math.sqrt(T)
        mu       = math.log(mean_dec) - 0.5 * sigma * sigma
        count    = sum(
            1 for _ in range(int(paths))
            if trigger_level_pct is not None
            and math.exp(random.gauss(mu, sigma)) * 100.0 >= float(trigger_level_pct)
        )
        return float(count) / float(paths)

    call_redemption_pct = float(note_data.get('issuer_call_redemption_amount_pct', 100.0))
    if note_data.get('final_redemption_amount_pct') is not None:
        maturity_redemption_pct = float(note_data['final_redemption_amount_pct'])
    else:
        redemption_amount   = float(note_data.get('redemption') or note_data.get('par') or 100.0)
        par_amount          = float(note_data.get('par', 100.0))
        maturity_redemption_pct = 100.0 * redemption_amount / par_amount if par_amount else 100.0

    call_scenarios = []
    for d in eligible_call_dates:
        sc = pv_to_horizon(d, call_redemption_pct)
        sc['horizon_date'] = d
        sc['triggered']    = None
        if callable_type == 'autocall_forward_dirty' and autocall_trigger and collateral_curve is not None:
            trigger_level = autocall_trigger.get('trigger_level_pct') or autocall_trigger.get('trigger_level')
            try:
                fd = estimate_forward_dirty_price(note_data.get('collateral', {}),
                                                   collateral_curve, collateral_curve_day_count, d)
            except Exception:
                fd = None
            sc['forward_dirty_pct'] = fd
            sc['triggered'] = (fd is not None and trigger_level is not None and fd >= float(trigger_level))
        call_scenarios.append(sc)

    maturity_scenario = pv_to_horizon(maturity_date, maturity_redemption_pct)

    def solve_model_yield_for_scenario(scenario, price_amount):
        if scenario is None or price_amount is None:
            return None
        freq_per_year = hullwhite.get_compounding_frequency_per_year(note_data)
        amounts, times = [], []
        for cf in scenario.get('cashflows', []):
            if cf.get('type') not in {'coupon', 'redemption'}:
                continue
            cf_date = ql.DateParser.parseISO(cf['date'])
            t       = note_day_count.yearFraction(eval_date, cf_date)
            if t <= 0.0:
                continue
            amounts.append(float(cf.get('amount', 0.0)))
            times.append(float(t))
        if not amounts:
            return None
        return hullwhite.solve_ytm_from_cashflows(
            price_amount=float(price_amount),
            cashflow_amounts=amounts,
            cashflow_times=times,
            freq_per_year=freq_per_year,
        )

    valuation_mode = note_data.get('valuation_mode', 'to_maturity')
    monte_info     = None
    selected       = None

    if callable_type == 'autocall_forward_dirty' and call_scenarios and autocall_trigger:
        trigger_level = autocall_trigger.get('trigger_level_pct') or autocall_trigger.get('trigger_level')
        monte_paths   = autocall_trigger.get('monte_carlo_paths')
        monte_vol     = autocall_trigger.get('monte_carlo_vol')
        if trigger_level is None and monte_paths and monte_vol is not None:
            sc       = min(call_scenarios, key=lambda s: int(s['horizon_date'].serialNumber()))
            fd_mean  = sc.get('forward_dirty_pct')
            trigger_candidate = note_data.get('collateral', {}).get('market_dirty_price')
            p_call   = monte_carlo_call_probability(fd_mean, monte_vol, sc['horizon_date'],
                                                     trigger_candidate, paths=monte_paths)
            monte_info = {
                'monte_paths':            int(monte_paths),
                'monte_vol':              float(monte_vol),
                'trigger_level_used_pct': trigger_candidate,
                'p_call':                 p_call,
                'call_date':              sc['horizon_date'].ISO(),
                'fd_mean_pct':            fd_mean,
            }
            expected_pv = p_call * sc['pv_note'] + (1.0 - p_call) * maturity_scenario['pv_note']
            selected = {
                'pv_note':           expected_pv,
                'pv_note_coupons':   sc.get('pv_note_coupons', 0.0) * p_call +
                                     maturity_scenario.get('pv_note_coupons', 0.0) * (1 - p_call),
                'pv_note_redemption':sc.get('pv_note_redemption', 0.0) * p_call +
                                     maturity_scenario.get('pv_note_redemption', 0.0) * (1 - p_call),
                'cashflows':         sc.get('cashflows', []) if p_call >= 0.5 else maturity_scenario.get('cashflows', []),
                'horizon_date':      sc['horizon_date'],
            }

    if selected is None:
        if valuation_mode == 'first_call' and call_scenarios:
            selected = min(call_scenarios, key=lambda s: int(s['horizon_date'].serialNumber()))
        elif valuation_mode == 'worst_call' and call_scenarios:
            selected = min(call_scenarios, key=lambda s: s['pv_note'])
        elif valuation_mode == 'call_and_maturity' and call_scenarios:
            triggered = [s for s in call_scenarios if s.get('triggered')]
            selected  = (min(triggered, key=lambda s: int(s['horizon_date'].serialNumber()))
                         if triggered else maturity_scenario)
        elif valuation_mode == 'to_maturity':
            selected = maturity_scenario
        else:
            raise ValueError(f'Unsupported valuation_mode: {valuation_mode}')

    first_call_scenario = (
        min(call_scenarios, key=lambda s: int(s['horizon_date'].serialNumber()))
        if call_scenarios else None
    )
    ytm = solve_model_yield_for_scenario(maturity_scenario, maturity_scenario.get('pv_note'))
    ytc = solve_model_yield_for_scenario(
        first_call_scenario,
        first_call_scenario.get('pv_note') if first_call_scenario else None,
    )

    return {
        'pv_note':             selected['pv_note'],
        'pv_note_coupons':     selected['pv_note_coupons'],
        'pv_note_redemption':  selected['pv_note_redemption'],
        'cashflows':           selected['cashflows'],
        'valuation_mode':      valuation_mode,
        'selected_call_date':  selected['horizon_date'].ISO(),
        'npv_to_first_call': (
            min(call_scenarios, key=lambda s: int(s['horizon_date'].serialNumber()))['pv_note']
            if call_scenarios else maturity_scenario['pv_note']
        ),
        'npv_to_worst_call': (
            min(call_scenarios, key=lambda s: s['pv_note'])['pv_note']
            if call_scenarios else maturity_scenario['pv_note']
        ),
        'npv_to_maturity':     maturity_scenario['pv_note'],
        'ytm':                 ytm,
        'ytc':                 ytc,
        'monte_info':          monte_info,
        'call_scenarios': [
            {
                'horizon_date':       s['horizon_date'].ISO(),
                'pv_note':            s['pv_note'],
                'pv_note_coupons':    s.get('pv_note_coupons', 0.0),
                'pv_note_redemption': s.get('pv_note_redemption', 0.0),
                'triggered':          s.get('triggered'),
                'forward_dirty_pct':  s.get('forward_dirty_pct'),
            }
            for s in call_scenarios
        ],
    }


def price_sensitivity(note_data, curve_json, n_steps=2, step_pct=0.10):
    """Return a vector of {spread_bp, pv_note_pct} at 2×n_steps+1 spread levels.

    The base spread (credit_spread_bp + collateral_spread_bp) is the centre.
    Each step shifts by step_pct × base (default 10%), so with n_steps=2 the
    levels are base × {0.80, 0.90, 1.00, 1.10, 1.20}.
    """
    base_spread = float(note_data.get('credit_spread_bp', 0.0)) + float(
        note_data.get('collateral_spread_bp') or note_data.get('collateral_spread') or 0.0
    )
    multipliers = [1.0 + (i - n_steps) * step_pct for i in range(2 * n_steps + 1)]
    sensitivity = []
    for m in multipliers:
        level = round(base_spread * m, 6)
        d = {**note_data, 'credit_spread_bp': level, 'collateral_spread_bp': 0.0}
        r = price_asset(d, curve_json, _skip_sensitivity=True)
        sensitivity.append({
            'spread_bp':    level,
            'pv_note_pct':  r['price_pct']['pv_note'],
        })
    return sensitivity


def price_asset(note_data, curve_json, _skip_sensitivity=False):
    evaluation_date = parse_date(note_data['evaluation_date'])

    note_curve_cfg,       note_curve_name       = select_note_curve(note_data, curve_json)
    collateral_curve_cfg, collateral_curve_name = select_collateral_curve(note_data, curve_json)
    note_curve,       note_curve_day_count       = build_discount_curve_and_dc(note_curve_cfg,       evaluation_date)
    collateral_curve, collateral_curve_day_count = build_discount_curve_and_dc(collateral_curve_cfg, evaluation_date)

    note_notional = float(note_data.get('note_notional', 100_000_000.0))
    issue_price   = float(note_data.get('issue_price', 100.0))

    note_leg      = _price_with_curve(note_data, note_curve, note_curve_day_count,
                                       collateral_curve=collateral_curve,
                                       collateral_curve_day_count=collateral_curve_day_count)
    collateral_leg = model_collateral_pv(note_data['collateral'], collateral_curve, collateral_curve_day_count)
    adjustments    = compute_valuation_adjustments(note_data, note_curve, note_curve_day_count)

    collateral_repo = note_data.get('collateral_repo', {}) or {}
    if collateral_repo.get('is_repo_financed') and collateral_repo.get('repo_purchase_price_pct') is not None:
        principal = float(note_data.get('collateral', {}).get('principal_amount', note_notional))
        collateral_leg['pv_collateral'] = principal * float(collateral_repo['repo_purchase_price_pct']) / 100.0

    swap_cfg  = note_data.get('swap', {})
    swap_mode = swap_cfg.get('mode', 'calibration_residual')
    if swap_mode in {'calibration_residual', 'explicit_cashflows'}:
        pv_swap = note_leg['pv_note'] - collateral_leg['pv_collateral'] + adjustments['pv_total_adjustments']
    else:
        raise ValueError(f'Unsupported swap mode: {swap_mode}')

    lhs = note_leg['pv_note']
    rhs = collateral_leg['pv_collateral'] + pv_swap - adjustments['pv_total_adjustments']
    s   = 100.0 / note_notional

    result = {
        'evaluation_date':              evaluation_date.ISO(),
        'note_discount_curve_name':     note_curve_name,
        'collateral_discount_curve_name': collateral_curve_name,
        'valuation_mode':               note_leg.get('valuation_mode', note_data.get('valuation_mode', 'to_maturity')),
        'selected_call_date':           note_leg.get('selected_call_date', parse_date(note_data['maturity_date']).ISO()),
        'issue_price':                  issue_price,
        'note_notional':                note_notional,
        'pv_note':                      note_leg['pv_note'],
        'pv_collateral':                collateral_leg['pv_collateral'],
        'pv_collateral_model':          collateral_leg['pv_collateral_model'],
        'collateral_valuation_method':  collateral_leg['valuation_method'],
        'pv_swap':                      pv_swap,
        'pv_adjustments':               adjustments,
        'identity_lhs_pv_note':         lhs,
        'identity_rhs_reconstructed':   rhs,
        'identity_error':               lhs - rhs,
        'npv_to_first_call':            note_leg.get('npv_to_first_call', note_leg['pv_note']),
        'npv_to_worst_call':            note_leg.get('npv_to_worst_call', note_leg['pv_note']),
        'npv_to_maturity':              note_leg.get('npv_to_maturity',   note_leg['pv_note']),
        'ytm':                          note_leg.get('ytm'),
        'ytc':                          note_leg.get('ytc'),
        'price_pct': {
            'pv_note':                   lhs * s,
            'pv_note_to_call':           note_leg.get('npv_to_first_call', lhs) * s,
            'pv_note_to_worst':          note_leg.get('npv_to_worst_call', lhs) * s,
            'pv_note_to_maturity':       note_leg.get('npv_to_maturity',   lhs) * s,
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
    result['ytm_promised'] = result['ytm']
    result['ytm_expected'] = result['ytm']
    if not _skip_sensitivity:
        result['sensitivity'] = price_sensitivity(note_data, curve_json)
    return result


def print_report(note_data, result):
    pct = result['price_pct']
    print(f"{note_data['description']} ({note_data['instrument_id']})")
    print(f"Evaluation date: {result['evaluation_date']}")
    print(f"Note discount curve: {result['note_discount_curve_name']}")
    print(f"Collateral discount curve: {result['collateral_discount_curve_name']}")
    print(f"Valuation mode: {result.get('valuation_mode', 'to_maturity')}")
    print(f"Selected call date: {result.get('selected_call_date', 'N/A')}")
    print(f"Issue price (%): {result['issue_price']:.4f}")
    print(f"PV(Note) %: {pct['pv_note']:.6f}")
    print(f"PV(Note) to_call %: {pct['pv_note_to_call']:.6f}")
    print(f"PV(Note) to_worst %: {pct['pv_note_to_worst']:.6f}")
    print(f"PV(Note) to_maturity %: {pct['pv_note_to_maturity']:.6f}")
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
    sensitivity = result.get('sensitivity')
    if sensitivity:
        print('Spread sensitivity (PV note %):')
        for s in sensitivity:
            marker = ' ◀' if abs(s['spread_bp'] - float(note_data.get('credit_spread_bp', 0.0))
                                   - float(note_data.get('collateral_spread_bp') or 0.0)) < 0.01 else ''
            print(f"  {s['spread_bp']:>8.2f} bp  →  {s['pv_note_pct']:.6f}%{marker}")
    monte = result.get('note_leg', {}).get('monte_info') or result.get('monte_info')
    if monte:
        p_call = monte.get('p_call')
        if p_call is not None:
            print(f"Call probability: {p_call:.2%}")
        print('Monte Carlo trigger info:')
        for k, v in monte.items():
            if k != 'p_call':
                print(f'  {k}: {v}')


# Keep backward-compatible aliases so any code that called spire.get_day_count etc. still works.
get_day_count                   = helper.get_day_count
get_calendar                    = helper.get_calendar
parse_date                      = helper.parse_date
load_json                       = helper.load_json
build_discount_curve            = helper.build_discount_curve
build_note_dates                = helper.build_note_dates
build_regular_schedule          = helper.build_regular_schedule
discount_factor_with_issuer_spread = helper.discount_factor_with_issuer_spread
inflation_factor                = helper.inflation_factor
select_note_curve               = helper.select_note_curve
select_collateral_curve         = helper.select_collateral_curve
model_collateral_pv             = helper.model_collateral_pv
spread_cost_from_schedule       = helper.spread_cost_from_schedule
compute_valuation_adjustments   = helper.compute_valuation_adjustments
infer_currency_from_isin        = helper.infer_currency_from_isin


def parse_args():
    parser = argparse.ArgumentParser(description='SPIRE collateral-mapped decomposition pricer')
    parser.add_argument('--bond-file',   default=str(BOND_FILE),   help='Path to SPIRE note JSON')
    parser.add_argument('--curve-file',  default=str(CURVE_FILE),  help='Path to swap curve JSON')
    return parser.parse_args()


if __name__ == '__main__':
    args       = parse_args()
    note_data  = load_json(Path(args.bond_file))
    curve_json = load_json(Path(args.curve_file))
    result     = price_asset(note_data, curve_json)
    print_report(note_data, result)
    pdf_path = pdf_report.create_pdf_report(
        model_name='spire',
        instrument_id=note_data.get('instrument_id', 'unknown'),
        input_payload=note_data,
        output_payload=result,
    )
    print(f'PDF report: {pdf_path}')
