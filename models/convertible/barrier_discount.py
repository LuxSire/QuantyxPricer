"""Barrier Discount Certificate pricer (SSPA product type 1210) — European barrier, Monte Carlo.

Prices barrier discount certificates where the barrier is observed only at maturity
(European observation), unlike barrier_convertible.py which monitors continuously.

Payoff at maturity
------------------
  Final price > barrier:  denomination (full cash repayment)
  Final price ≤ barrier:  conversion_ratio × final_price  (physical delivery)

  conversion_ratio = denomination / strike_level  (auto-derived if not set)

Quanto adjustment
-----------------
  When the underlying is quoted in a different currency from the settlement currency,
  apply a quanto drift correction:
    drift = r_settlement − dividend_yield − quanto_adjustment
    quanto_adjustment = rho_stock_fx × sigma_stock × sigma_fx

Required JSON fields
--------------------
  instrument_id     ISIN or internal identifier
  evaluation_date   Pricing date (DD-MM-YYYY or YYYY-MM-DD)
  maturity_date     DD-MM-YYYY or YYYY-MM-DD
  denomination      Notional / face value (e.g. 1000)

  underlyings       List with one underlying object:
    name / instrument_id   Label
    initial_fixing         Spot price at trade date
    current_price          Current spot price
    volatility             Annualised volatility (decimal)
    dividend_yield         Annualised dividend yield (decimal)
    strike_pct             Strike as % of initial fixing (e.g. 100)
    barrier_pct            European barrier as % of initial fixing (e.g. 120)
    conversion_ratio       Optional — derived as denomination / strike if absent

Optional JSON fields
--------------------
  quanto_adjustment   Annualised quanto drift correction (default 0.0)
  coupon_schedule     List of {date, amount} for any periodic coupon payments
  mc_time_steps       Number of time steps per path (default 360)
  mc_num_paths        Number of Monte Carlo paths (default 5000)
  mc_seed             Random seed for reproducibility (default 42)
  issuer_spread_bp    Z-spread override; falls back to credit_spread_bp
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
        build_corr_matrix, resolve_underlyings,
        _get_ul_barrier, _get_ul_strike,
    )
except (ModuleNotFoundError, ImportError):
    import hullwhite
    from helper import today_date_string, normalize_rate, parse_date, load_json
    from convertible import (
        build_correlated_equity_paths,
        get_cashflows, get_accrued_amount, discount_date,
        build_corr_matrix, resolve_underlyings,
        _get_ul_barrier, _get_ul_strike,
    )

BASE_DIR = Path(__file__).resolve().parent.parent.parent
PROJECT_ROOT = BASE_DIR
ASSETS_DIR = PROJECT_ROOT / 'assets'
CURVES_DIR = PROJECT_ROOT / 'curves'
CURVE_FILE = CURVES_DIR / 'swap_curves.json'


def _build_ul_meta(bond_data, underlyings_list, denomination, quanto_adj):
    """Like build_ul_meta but applies the quanto drift adjustment and derives
    conversion_ratio as denomination/strike when not explicitly provided."""
    ul_meta = []
    ul_params = []
    for i, ul in enumerate(underlyings_list):
        s0 = float(ul.get('initial_fixing_level', 0.0))
        if s0 <= 0:
            raise ValueError(f'underlyings[{i}].initial_fixing_level must be positive')

        _uv_key = bond_data.get(f'underlying_volatility_{i}')
        _uv_fallback = bond_data.get('underlying_volatility') if i == 0 else None
        _uv = _uv_key if _uv_key is not None else _uv_fallback
        vol       = _uv if _uv is not None else normalize_rate(ul.get('volatility', 0.30))
        div_yield = normalize_rate(ul.get('dividend_yield', 0.0))
        # Quanto-adjusted drift: use explicitly set drift if provided,
        # otherwise the adjustment is applied on top of zero drift
        # (r_settlement is already baked into the discount curve; we only
        # need the cross-currency correction here).
        base_drift = normalize_rate(ul.get('drift', 0.0))
        drift_i   = base_drift - quanto_adj

        barrier_i = _get_ul_barrier(ul, s0)
        strike_i  = _get_ul_strike(ul, s0)

        # Conversion ratio: use explicit value, else derive from denomination/strike
        conv_i = float(ul.get('conversion_ratio') or 0.0)
        if conv_i <= 0 and strike_i > 0:
            conv_i = denomination / strike_i

        ul_meta.append((ul, s0, barrier_i, strike_i, conv_i, vol))
        ul_params.append((s0, drift_i, div_yield, vol))

    return ul_meta, ul_params


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

    # Quanto drift correction: rho_stock_fx * sigma_stock * sigma_fx
    quanto_adj = float(bond_data.get('quanto_adjustment', 0.0))

    maturity_date = parse_date(bond_data.get('maturity_date') or bond_data.get('end_date') or today_date_string())
    maturity_time = ql.Actual365Fixed().yearFraction(evaluation_date, maturity_date)
    if maturity_time <= 0:
        raise ValueError('maturity_date must be after evaluation_date')

    # Discount certificates typically pay no coupon, but we read the schedule
    # for flexibility (e.g. conditional coupon variants).
    cashflows = get_cashflows(bond_data, evaluation_date)
    coupon_pv = sum(cf['amount'] * discount_date(discount_curve, cf['date']) for cf in cashflows)
    accrued_amount = get_accrued_amount(bond_data, cashflows, evaluation_date)

    N = len(underlyings_list)
    ul_meta, ul_params = _build_ul_meta(bond_data, underlyings_list, denomination, quanto_adj)
    corr_matrix = build_corr_matrix(bond_data, N)

    all_times, all_paths = build_correlated_equity_paths(
        ul_params, maturity_time, time_steps, num_paths, seed, corr_matrix
    )

    ul_paths = [
        (ul, s0, bar, stk, conv, all_paths[i], vol)
        for i, (ul, s0, bar, stk, conv, vol) in enumerate(ul_meta)
    ]

    # European barrier: check final fixing level only (last time step).
    # Barrier event = any underlying's final price is at or below its barrier.
    redemption_values = []
    for p in range(num_paths):
        barrier_event = any(
            float(paths_i[p][-1]) <= barrier_i
            for (_, _s0, barrier_i, _, _, paths_i, _) in ul_paths
        )

        if not barrier_event:
            redemption_values.append(denomination)
            continue

        # Worst-of physical delivery: find underlying with lowest final/initial ratio
        worst_perf = None
        worst_idx = 0
        for idx, (_, s0_i, _, _, _, paths_i, _vol) in enumerate(ul_paths):
            perf = paths_i[p][-1] / s0_i if s0_i > 0 else 0.0
            if worst_perf is None or perf < worst_perf:
                worst_perf = perf
                worst_idx = idx

        _, _, _, _, conv_w, paths_w, _ = ul_paths[worst_idx]
        redemption_values.append(conv_w * float(paths_w[p][-1]))

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

    result = {
        'selected_npv': npv,
        'npv': npv,
        'clean_price': npv - accrued_amount,
        'dirty_price': npv,
        'accrued_amount': accrued_amount,
        'issuer_spread_bp': issuer_spread_bp,
        'quanto_adjustment': quanto_adj,
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
            'pv_note_to_maturity': price_pct_val,
        },
        'npv_to_maturity': npv,
    }
    if not _skip_sensitivity:
        result['sensitivity'] = price_sensitivity(bond_data, curve_json)
    return result


def print_result(bond_data, result):
    print(f"{bond_data.get('description', bond_data.get('instrument_id'))} ({bond_data.get('instrument_id')})")
    print(f"Evaluation date: {result['evaluation_date']}")
    print(f"Maturity date: {result['maturity_date']}")
    print(f"Issuer spread: {result['issuer_spread_bp']:.2f} bp")
    print(f"Quanto adjustment: {result['quanto_adjustment']:.4f}")
    for ul in result.get('underlyings', []):
        print(f"  Underlying: {ul.get('name')}  vol={ul.get('volatility'):.4f}  barrier={ul.get('barrier_level')}  strike={ul.get('strike_level')}  conv={ul.get('conversion_ratio'):.4f}")
    print(f"Monte Carlo paths: {result['mc_num_paths']}")
    print(f"NPV: {result['npv']:.4f}")
    print(f"Price (% of denomination): {result['price_pct']['pv_note']:.4f}%")
    print(f"Clean price: {result['clean_price']:.4f}")
    if result.get('sensitivity'):
        print('Equity path sensitivity (% of initial fixing → PV%):')
        print(f"  {'Level%':>8}  {'PV(Note)%':>12}")
        for s in result['sensitivity']:
            print(f"  {s['spread_bp']:>8.1f}  {s['pv_note_pct']:>12.6f}")
    print()


def parse_args():
    parser = argparse.ArgumentParser(description='Price barrier discount certificates.')
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
    print_result(bond_data, result)


if __name__ == '__main__':
    main()
