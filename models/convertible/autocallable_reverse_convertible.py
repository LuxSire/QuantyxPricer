"""Autocallable Reverse Convertible pricer — worst-of, continuous barrier, Monte Carlo.

Prices autocallable reverse convertible notes on one or multiple underlyings.
At each autocall observation date, if all underlyings are at or above the autocall
level the note redeems early at denomination.  If the note is never autocalled, the
barrier and worst-of logic at maturity is identical to barrier_convertible.py.

Payoff logic (per path)
-----------------------
  For each autocall date (ascending chronological order):
    If all underlyings_i(t) ≥ autocall_level_i → redeem at denomination, discount to eval date
  If no autocall triggered and barrier was never breached:
    Full cash repayment (denomination)
  If no autocall triggered and barrier was breached at any point:
    worst_of(conversion_ratio_i × final_price_i)  — physical delivery of worst performer

Required JSON fields
--------------------
  instrument_id       ISIN or internal identifier
  evaluation_date     Pricing date (DD-MM-YYYY or YYYY-MM-DD)
  maturity_date       DD-MM-YYYY or YYYY-MM-DD
  denomination        Notional / face value (e.g. 1000)
  coupon_schedule     List of {date, amount} objects for periodic coupon payments
  autocall_schedule   List of {date, autocall_level_pct} observation events

  underlyings         List of underlying objects, each with:
    name / instrument_id   Label
    initial_fixing         Spot price at trade date
    current_price          Current spot price
    volatility             Annualised volatility (decimal)
    dividend_yield         Annualised dividend yield (decimal)
    strike_pct             Strike as % of initial fixing (e.g. 100)
    barrier_pct            Barrier level as % of initial fixing (e.g. 65)
    conversion_ratio       Optional — derived as denomination / strike if absent

Optional JSON fields
--------------------
  mc_time_steps               Number of time steps per path (default 360)
  mc_num_paths                Number of Monte Carlo paths (default 5000)
  mc_seed                     Random seed for reproducibility (default 42)
  issuer_spread_bp            Z-spread override; falls back to credit_spread_bp
  barrier_observation_period  Dict with start/end dates to restrict the barrier window
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
        _get_ul_barrier,
    )
    from models.convertible.barrier_convertible import get_barrier_range
except (ModuleNotFoundError, ImportError):
    import hullwhite
    from helper import today_date_string, normalize_rate, parse_date, load_json
    from convertible import (
        build_correlated_equity_paths,
        get_cashflows, get_accrued_amount, discount_date,
        build_ul_meta, build_corr_matrix, resolve_underlyings,
        _get_ul_barrier,
    )
    from barrier_convertible import get_barrier_range

BASE_DIR = Path(__file__).resolve().parent.parent.parent
PROJECT_ROOT = BASE_DIR
ASSETS_DIR = PROJECT_ROOT / 'assets'
CURVES_DIR = PROJECT_ROOT / 'curves'
CURVE_FILE = CURVES_DIR / 'swap_curves.json'


def _parse_autocall_schedule(bond_data, evaluation_date, all_times):
    """Return list of (obs_date, time_idx, autocall_level_abs_per_underlying).

    autocall_level in the schedule can be:
      - a scalar pct (e.g. 1.0 = 100 %) applied uniformly
      - per-underlying list under 'autocall_levels'
    """
    observations = []
    dc = ql.Actual365Fixed()
    for entry in bond_data.get('autocall_schedule', []):
        obs_date = parse_date(entry['date'])
        if obs_date <= evaluation_date:
            continue
        obs_time = dc.yearFraction(evaluation_date, obs_date)
        time_idx = int(np.argmin(np.abs(all_times - obs_time)))
        level_pct = normalize_rate(entry.get('autocall_level', 1.0))
        observations.append((obs_date, time_idx, level_pct))
    return observations


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

    autocall_obs = _parse_autocall_schedule(bond_data, evaluation_date, all_times)
    barrier_indices = np.where((all_times >= barrier_start) & (all_times <= barrier_end))[0]

    redemption_pvs = []
    n_called = 0
    for p in range(num_paths):
        # Check autocall dates in chronological order
        called = False
        for obs_date, time_idx, level_pct in autocall_obs:
            if all(
                paths_i[p][time_idx] >= s0_i * level_pct
                for (_, s0_i, _, _, _, paths_i, _) in ul_paths
            ):
                redemption_pvs.append(denomination * discount_date(discount_curve, obs_date))
                called = True
                break

        if called:
            n_called += 1
            continue

        # Not called — apply barrier + worst-of at maturity
        any_hit = any(
            np.min(paths_i[p][barrier_indices] if barrier_indices.size > 0 else paths_i[p]) <= barrier_i
            for (_, _s0, barrier_i, _, _, paths_i, _) in ul_paths
        )

        if not any_hit:
            redemption_pvs.append(denomination * discount_date(discount_curve, maturity_date))
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
        rv = conv_w * final_w if final_w <= strike_w else denomination
        redemption_pvs.append(rv * discount_date(discount_curve, maturity_date))

    expected_redemption_pv = float(np.mean(redemption_pvs))
    npv = expected_redemption_pv + coupon_pv
    price_pct_val = npv / denomination * 100.0 if denomination > 0 else 0.0
    autocall_probability = n_called / num_paths if num_paths > 0 else 0.0

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
        'autocall_observations': len(autocall_obs),
        'autocall_probability': autocall_probability,
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


def print_report(bond_data, result):
    print(f"{bond_data.get('description', bond_data.get('instrument_id'))} ({bond_data.get('instrument_id')})")
    print(f"Evaluation date: {result['evaluation_date']}")
    print(f"Maturity date: {result['maturity_date']}")
    print(f"Autocall observations: {result['autocall_observations']}")
    if result.get('autocall_probability') is not None:
        print(f"Autocall probability: {result['autocall_probability']:.2%}")
    print(f"Issuer spread: {result['issuer_spread_bp']:.2f} bp")
    for ul in result.get('underlyings', []):
        print(f"  Underlying: {ul.get('name')}  vol={ul.get('volatility'):.4f}  barrier={ul.get('barrier_level')}  strike={ul.get('strike_level')}  conv={ul.get('conversion_ratio')}")
    print(f"Monte Carlo paths: {result['mc_num_paths']}")
    print(f"NPV: {result['npv']:.4f}")
    print(f"Clean price: {result['clean_price']:.4f}")
    print(f"Accrued amount: {result['accrued_amount']:.4f}")
    print()


def parse_args():
    parser = argparse.ArgumentParser(description='Price autocallable reverse convertible products.')
    parser.add_argument('--bond-file', required=True, help='Path to bond JSON input file')
    parser.add_argument('--curve-file', default=str(CURVE_FILE), help='Path to curve JSON input file')
    parser.add_argument('--issuer-spread-bp', type=float, default=None)
    parser.add_argument('--time-steps', type=int, default=None)
    parser.add_argument('--num-paths', type=int, default=None)
    parser.add_argument('--seed', type=int, default=None)
    return parser.parse_args()


def main():
    args = parse_args()
    bond_data = load_json(Path(args.bond_file))
    curve_json = load_json(Path(args.curve_file))
    if args.time_steps is not None:
        bond_data['mc_time_steps'] = args.time_steps
    if args.num_paths is not None:
        bond_data['mc_num_paths'] = args.num_paths
    if args.seed is not None:
        bond_data['mc_seed'] = args.seed
    result = price_asset(bond_data, curve_json, issuer_spread_bp=args.issuer_spread_bp)
    print_report(bond_data, result)


if __name__ == '__main__':
    main()
