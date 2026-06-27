"""Simple convertible / reverse convertible pricer — worst-of, no barrier, Monte Carlo.

Prices both reverse convertible and standard convertible payoffs on one or multiple
underlyings.  No barrier is monitored (see barrier_convertible.py for that variant).

Payoff structures (set via convertible_type field)
--------------------------------------------------
  'reverse'  (default) — Reverse Convertible:
      At maturity, if the worst-of underlying finishes below its strike,
      the investor receives physical delivery (conversion_ratio × final_price)
      instead of denomination.  The periodic coupon compensates for this
      embedded short put position.

  'standard' — Standard Convertible:
      At maturity, if the worst-of underlying finishes above its strike,
      the investor converts and receives max(denomination, conversion_ratio × final_price).
      The coupon is typically lower, reflecting the embedded long call value.

Required JSON fields
--------------------
  instrument_id      ISIN or internal identifier
  evaluation_date    Pricing date (DD-MM-YYYY or YYYY-MM-DD)
  maturity_date      DD-MM-YYYY or YYYY-MM-DD
  denomination       Notional / face value (e.g. 1000)
  coupon_schedule    List of {date, amount} objects for periodic coupon payments

  underlyings        List of underlying objects, each with:
    name / instrument_id   Label
    initial_fixing         Spot price at trade date
    current_price          Current spot price
    volatility             Annualised volatility (decimal)
    dividend_yield         Annualised dividend yield (decimal)
    strike_pct             Strike as % of initial fixing (e.g. 100)
    conversion_ratio       Optional — derived as denomination / strike if absent

Optional JSON fields
--------------------
  convertible_type   reverse (default) | standard
  mc_time_steps      Number of time steps per path (default 360)
  mc_num_paths       Number of Monte Carlo paths (default 5000)
  mc_seed            Random seed for reproducibility (default 42)
  issuer_spread_bp   Z-spread override; falls back to credit_spread_bp
  correlation        Correlation matrix for multi-underlying paths (list of lists)
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
        worst_of_redemption, standard_convertible_redemption,
    )
except (ModuleNotFoundError, ImportError):
    import hullwhite
    from helper import today_date_string, normalize_rate, parse_date, load_json
    from convertible import (
        build_correlated_equity_paths,
        get_cashflows, get_accrued_amount, discount_date,
        build_ul_meta, build_corr_matrix, resolve_underlyings,
        worst_of_redemption, standard_convertible_redemption,
    )

BASE_DIR = Path(__file__).resolve().parent.parent.parent
PROJECT_ROOT = BASE_DIR
ASSETS_DIR = PROJECT_ROOT / 'assets'
CURVES_DIR = PROJECT_ROOT / 'curves'
CURVE_FILE = CURVES_DIR / 'swap_curves.json'


def _scaled_bond_data(bond_data, multiplier):
    d = dict(bond_data)
    if d.get('underlyings'):
        d['underlyings'] = [
            {**ul, 'initial_fixing_level': float(ul.get('initial_fixing_level', 0.0)) * multiplier}
            for ul in d['underlyings']
        ]
    if d.get('underlying') and float((d.get('underlying') or {}).get('initial_fixing_level', 0.0)) > 0:
        d['underlying'] = {**d['underlying'], 'initial_fixing_level': float(d['underlying']['initial_fixing_level']) * multiplier}
    return d


def price_sensitivity(bond_data, curve_json, n_steps=2, step_pct=0.10):
    multipliers = [1.0 + (i - n_steps) * step_pct for i in range(2 * n_steps + 1)]
    sensitivity = []
    for m in multipliers:
        r = price_asset(_scaled_bond_data(bond_data, m), curve_json, _skip_sensitivity=True)
        sensitivity.append({'spread_bp': round(m * 100, 1), 'pv_note_pct': r['price_pct']['pv_note']})
    return sensitivity


def price_asset(bond_data, curve_json, issuer_spread_bp=None, _skip_sensitivity=False):
    convertible_type = str(bond_data.get('convertible_type', 'reverse')).strip().lower()
    if convertible_type not in ('reverse', 'standard'):
        raise ValueError(f"convertible_type must be 'reverse' or 'standard', got '{convertible_type}'")

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

    redemption_fn = (
        worst_of_redemption if convertible_type == 'reverse'
        else standard_convertible_redemption
    )
    redemption_values = [redemption_fn(ul_paths, p, denomination) for p in range(num_paths)]

    df_maturity = discount_date(discount_curve, maturity_date)
    expected_redemption = float(np.mean(redemption_values)) * df_maturity
    npv = expected_redemption + coupon_pv
    price_pct_val = npv / denomination * 100.0 if denomination > 0 else 0.0

    ul_summary = [
        {
            'name': ul.get('name') or ul.get('instrument_id', f'underlying_{i}'),
            'initial_fixing_level': s0_i,
            'strike_level': str_i,
            'conversion_ratio': con_i,
            'volatility': vol_i,
        }
        for i, (ul, s0_i, _, str_i, con_i, _, vol_i) in enumerate(ul_paths)
    ]

    _, s0_0, _, strike_0, conv_0, _, _ = ul_paths[0]

    result = {
        'selected_npv': npv,
        'npv': npv,
        'clean_price': npv - accrued_amount,
        'dirty_price': npv,
        'accrued_amount': accrued_amount,
        'issuer_spread_bp': issuer_spread_bp,
        'convertible_type': convertible_type,
        'mc_time_steps': time_steps,
        'mc_num_paths': num_paths,
        'mc_seed': seed,
        'evaluation_date': evaluation_date.ISO(),
        'maturity_date': maturity_date.ISO(),
        'underlyings': ul_summary,
        'strike_level': strike_0,
        'conversion_ratio': conv_0,
        'price_pct': {
            'pv_note': price_pct_val,
            'pv_note_to_maturity': price_pct_val,
        },
        'npv_to_maturity': npv,
    }
    if not _skip_sensitivity:
        result['sensitivity'] = price_sensitivity(bond_data, curve_json)
    return result


def print_result(bond_data, result):
    ctype = result.get('convertible_type', 'reverse')
    label = 'Standard Convertible' if ctype == 'standard' else 'Reverse Convertible'
    print(f"{bond_data.get('description', bond_data.get('instrument_id'))} ({bond_data.get('instrument_id')}) [{label}]")
    print(f"Evaluation date: {result['evaluation_date']}")
    print(f"Maturity date: {result['maturity_date']}")
    print(f"Issuer spread: {result['issuer_spread_bp']:.2f} bp")
    for ul in result.get('underlyings', []):
        print(f"  Underlying: {ul.get('name')}  vol={ul.get('volatility'):.4f}  strike={ul.get('strike_level')}  conv={ul.get('conversion_ratio')}")
    print(f"Monte Carlo paths: {result['mc_num_paths']}")
    print(f"NPV: {result['npv']:.4f}")
    print(f"Clean price: {result['clean_price']:.4f}")
    print(f"Accrued amount: {result['accrued_amount']:.4f}")
    if result.get('sensitivity'):
        print('Equity path sensitivity (% of initial fixing → PV%):')
        print(f"  {'Level%':>8}  {'PV(Note)%':>12}")
        for s in result['sensitivity']:
            print(f"  {s['spread_bp']:>8.1f}  {s['pv_note_pct']:>12.6f}")
    print()


def parse_args():
    parser = argparse.ArgumentParser(description='Price simple convertible or reverse convertible products.')
    parser.add_argument('--bond-file', required=True, help='Path to bond JSON input file')
    parser.add_argument('--curve-file', default=str(CURVE_FILE), help='Path to curve JSON input file')
    parser.add_argument('--convertible-type', choices=['reverse', 'standard'], default=None,
                        help='Override convertible_type from JSON (reverse or standard)')
    parser.add_argument('--issuer-spread-bp', type=float, default=None)
    parser.add_argument('--time-steps', type=int, default=None)
    parser.add_argument('--num-paths', type=int, default=None)
    parser.add_argument('--seed', type=int, default=None)
    return parser.parse_args()


def main():
    args = parse_args()
    bond_data = load_json(Path(args.bond_file))
    curve_json = load_json(Path(args.curve_file))
    if args.convertible_type is not None:
        bond_data['convertible_type'] = args.convertible_type
    if args.time_steps is not None:
        bond_data['mc_time_steps'] = args.time_steps
    if args.num_paths is not None:
        bond_data['mc_num_paths'] = args.num_paths
    if args.seed is not None:
        bond_data['mc_seed'] = args.seed
    result = price_asset(bond_data, curve_json, issuer_spread_bp=args.issuer_spread_bp)
    print_result(bond_data, result)


if __name__ == '__main__':
    main()
