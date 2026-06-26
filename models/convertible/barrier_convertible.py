"""Barrier Reverse Convertible pricer — worst-of, continuous barrier, Monte Carlo.

Prices barrier reverse convertible notes (also called barrier reverse exchangeables)
on one or multiple underlyings using GBM paths with optional Cholesky correlation.
The barrier is monitored continuously over a configurable observation window.

Payoff at maturity
------------------
  Barrier never breached:  denomination (full cash repayment)
  Barrier breached at any point during the observation window:
    worst_of(conversion_ratio_i × final_price_i)  — physical delivery of worst performer

Coupons are fixed amounts paid periodically according to coupon_schedule.

Required JSON fields
--------------------
  instrument_id     ISIN or internal identifier
  evaluation_date   Pricing date (DD-MM-YYYY or YYYY-MM-DD)
  maturity_date     DD-MM-YYYY or YYYY-MM-DD
  denomination      Notional / face value (e.g. 1000)
  coupon_schedule   List of {date, amount} objects for periodic coupon payments

  underlyings       List of underlying objects, each with:
    name / instrument_id   Label
    initial_fixing         Spot price at trade date
    current_price          Current spot price
    volatility             Annualised volatility (decimal)
    dividend_yield         Annualised dividend yield (decimal)
    strike_pct             Strike as % of initial fixing (e.g. 100)
    barrier_pct            Barrier level as % of initial fixing (e.g. 65)
    conversion_ratio       Optional — if absent, derived as denomination / strike

Optional JSON fields
--------------------
  mc_time_steps               Number of time steps per path (default 360)
  mc_num_paths                Number of Monte Carlo paths (default 5000)
  mc_seed                     Random seed for reproducibility (default 42)
  issuer_spread_bp            Z-spread override; falls back to credit_spread_bp
  barrier_observation_period  Dict with start/end dates to restrict the observation window
  correlation                 Correlation matrix for multi-underlying paths (list of lists)
"""

import argparse
from pathlib import Path

import numpy as np
import QuantLib as ql

try:
    from models import hullwhite
    from models.helper import today_date_string, normalize_rate, parse_date, load_json
    from models.convertible.convertible import (
        build_correlated_equity_paths,
        get_cashflows, get_accrued_amount, discount_date,
        build_ul_meta, build_corr_matrix, resolve_underlyings,
    )
except (ModuleNotFoundError, ImportError):
    import hullwhite
    from helper import today_date_string, normalize_rate, parse_date, load_json
    from convertible import (
        build_correlated_equity_paths,
        get_cashflows, get_accrued_amount, discount_date,
        build_ul_meta, build_corr_matrix, resolve_underlyings,
    )

try:
    from reporting import pdf_report
except (ModuleNotFoundError, ImportError):
    import reporting.pdf_report as pdf_report

BASE_DIR = Path(__file__).resolve().parent.parent.parent
PROJECT_ROOT = BASE_DIR
ASSETS_DIR = PROJECT_ROOT / 'assets'
CURVES_DIR = PROJECT_ROOT / 'curves'
CURVE_FILE = CURVES_DIR / 'swap_curves.json'
BOND_FILE = ASSETS_DIR / 'CH1493992296.json'


def get_barrier_range(bond_data, evaluation_date, maturity_date):
    period = bond_data.get('barrier_observation_period', {})
    if not period:
        return 0.0, ql.Actual365Fixed().yearFraction(evaluation_date, maturity_date)
    start = parse_date(period.get('start')) if period.get('start') else evaluation_date
    end = parse_date(period.get('end')) if period.get('end') else maturity_date
    dc = ql.Actual365Fixed()
    start_time = max(0.0, dc.yearFraction(evaluation_date, start))
    end_time = min(dc.yearFraction(evaluation_date, end), dc.yearFraction(evaluation_date, maturity_date))
    return start_time, end_time


def price_asset(bond_data, curve_json, issuer_spread_bp=None):
    evaluation_date = parse_date(bond_data.get('evaluation_date', today_date_string()))
    ql.Settings.instance().evaluationDate = evaluation_date

    discount_curve_cfg = hullwhite.select_discount_curve_config(curve_json, bond_data)
    discount_curve = hullwhite.build_discount_curve(discount_curve_cfg, evaluation_date)

    if issuer_spread_bp is None:
        issuer_spread_bp = float(bond_data.get('issuer_spread_bp', bond_data.get('credit_spread_bp', 0.0)))
    if issuer_spread_bp != 0.0:
        discount_curve = hullwhite.build_spreaded_curve(discount_curve, issuer_spread_bp)

    underlyings_list = resolve_underlyings(bond_data)
    denomination = float(bond_data.get('nominal_price') or bond_data.get('face_value') or bond_data.get('denomination') or 1000.0)
    time_steps = int(bond_data.get('mc_time_steps', 360))
    num_paths  = int(bond_data.get('mc_num_paths', 5000))
    seed       = int(bond_data.get('mc_seed', 42))

    maturity_date = parse_date(bond_data.get('maturity_date') or bond_data.get('end_date') or today_date_string())
    maturity_time = ql.Actual365Fixed().yearFraction(evaluation_date, maturity_date)
    if maturity_time <= 0:
        raise ValueError('maturity_date must be after evaluation_date')

    cashflows = get_cashflows(bond_data, evaluation_date)
    coupon_pv = sum(cf['amount'] * discount_date(discount_curve, cf['date']) for cf in cashflows)
    accrued_amount = get_accrued_amount(bond_data, cashflows, evaluation_date)
    barrier_start, barrier_end = get_barrier_range(bond_data, evaluation_date, maturity_date)

    N = len(underlyings_list)
    ul_meta, ul_params = build_ul_meta(bond_data, underlyings_list)
    corr_matrix = build_corr_matrix(bond_data, N)

    all_times, all_paths = build_correlated_equity_paths(
        ul_params, maturity_time, time_steps, num_paths, seed, corr_matrix
    )

    ul_paths = [
        (ul, s0, bar, stk, conv, all_paths[i], vol)
        for i, (ul, s0, bar, stk, conv, vol) in enumerate(ul_meta)
    ]

    barrier_indices = np.where((all_times >= barrier_start) & (all_times <= barrier_end))[0]

    redemption_values = []
    for p in range(num_paths):
        any_hit = any(
            np.min(paths_i[p][barrier_indices] if barrier_indices.size > 0 else paths_i[p]) <= barrier_i
            for (_, _s0, barrier_i, _, _, paths_i, _) in ul_paths
        )

        if not any_hit:
            redemption_values.append(denomination)
            continue

        worst_perf = None
        worst_idx = 0
        for idx, (_, s0_i, _, _, _, paths_i, _vol) in enumerate(ul_paths):
            perf = paths_i[p][-1] / s0_i if s0_i > 0 else 0.0
            if worst_perf is None or perf < worst_perf:
                worst_perf = perf
                worst_idx = idx

        _, _, _, strike_w, conv_w, paths_w, _ = ul_paths[worst_idx]
        final_w = float(paths_w[p][-1])
        redemption_values.append(conv_w * final_w if final_w <= strike_w else denomination)

    df_maturity = discount_date(discount_curve, maturity_date)
    expected_redemption = float(np.mean(redemption_values)) * df_maturity
    npv = expected_redemption + coupon_pv
    price_pct_val = npv / denomination * 100.0 if denomination > 0 else 0.0

    ul_summary = [
        {
            'name': ul.get('name') or ul.get('instrument_id', f'underlying_{i}'),
            'initial_fixing_level': s0_i,
            'barrier_level': bar_i,
            'strike_level': str_i,
            'conversion_ratio': con_i,
            'volatility': vol_i,
        }
        for i, (ul, s0_i, bar_i, str_i, con_i, _, vol_i) in enumerate(ul_paths)
    ]

    _, s0_0, barrier_0, strike_0, conv_0, _, _ = ul_paths[0]

    return {
        'selected_npv': npv,
        'npv': npv,
        'clean_price': npv - accrued_amount,
        'dirty_price': npv,
        'accrued_amount': accrued_amount,
        'issuer_spread_bp': issuer_spread_bp,
        'mc_time_steps': time_steps,
        'mc_num_paths': num_paths,
        'mc_seed': seed,
        'evaluation_date': evaluation_date.ISO(),
        'maturity_date': maturity_date.ISO(),
        'underlyings': ul_summary,
        'barrier_level': barrier_0,
        'strike_level': strike_0,
        'conversion_ratio': conv_0,
        'price_pct': {
            'pv_note': price_pct_val,
            'pv_note_to_worst_call': price_pct_val,
            'pv_note_to_maturity': price_pct_val,
        },
        'npv_to_worst_call': npv,
        'npv_to_maturity': npv,
    }


def print_result(bond_data, result):
    print(f"{bond_data.get('description', bond_data.get('instrument_id'))} ({bond_data.get('instrument_id')})")
    print(f"Evaluation date: {result['evaluation_date']}")
    print(f"Maturity date: {result['maturity_date']}")
    print(f"Issuer spread: {result['issuer_spread_bp']:.2f} bp")
    for ul in result.get('underlyings', []):
        print(f"  Underlying: {ul.get('name')}  vol={ul.get('volatility'):.4f}  barrier={ul.get('barrier_level')}  strike={ul.get('strike_level')}  conv={ul.get('conversion_ratio')}")
    print(f"Monte Carlo time steps: {result['mc_time_steps']}")
    print(f"Monte Carlo paths: {result['mc_num_paths']}")
    print(f"Monte Carlo seed: {result['mc_seed']}")
    print(f"NPV: {result['npv']:.4f}")
    print(f"Clean price: {result['clean_price']:.4f}")
    print(f"Dirty price: {result['dirty_price']:.4f}")
    print(f"Accrued amount: {result['accrued_amount']:.4f}")
    print()


def parse_args():
    parser = argparse.ArgumentParser(description='Price barrier convertible products using an equity Monte Carlo model.')
    parser.add_argument('--bond-file', default=str(BOND_FILE), help='Path to bond JSON input file')
    parser.add_argument('--curve-file', default=str(CURVE_FILE), help='Path to curve JSON input file')
    parser.add_argument('--issuer-spread-bp', type=float, default=None, help='Optional override for issuer spread in basis points')
    parser.add_argument('--time-steps', type=int, default=None, help='Override Monte Carlo time steps')
    parser.add_argument('--num-paths', type=int, default=None, help='Override Monte Carlo number of paths')
    parser.add_argument('--seed', type=int, default=None, help='Override Monte Carlo random seed')
    return parser.parse_args()


def apply_mc_overrides(bond_data, args):
    data = dict(bond_data)
    if args.time_steps is not None:
        data['mc_time_steps'] = args.time_steps
    if args.num_paths is not None:
        data['mc_num_paths'] = args.num_paths
    if args.seed is not None:
        data['mc_seed'] = args.seed
    return data


def main():
    args = parse_args()
    bond_data = load_json(Path(args.bond_file))
    curve_json = load_json(Path(args.curve_file))
    bond_data = apply_mc_overrides(bond_data, args)
    result = price_asset(bond_data, curve_json, issuer_spread_bp=args.issuer_spread_bp)
    print_result(bond_data, result)


if __name__ == '__main__':
    main()
