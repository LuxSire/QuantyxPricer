import argparse
import math
from pathlib import Path

import numpy as np
import QuantLib as ql

try:
    from models import hullwhite
except ModuleNotFoundError:
    import hullwhite

try:
    from models.helper import today_date_string, today_date_string_iso, normalize_rate, parse_date
except ModuleNotFoundError:
    from helper import today_date_string, today_date_string_iso, normalize_rate, parse_date

try:
    from reporting import pdf_report
except ModuleNotFoundError:
    import reporting.pdf_report as pdf_report

BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent
ASSETS_DIR = PROJECT_ROOT / 'assets'
CURVES_DIR = PROJECT_ROOT / 'curves'
CURVE_FILE = CURVES_DIR / 'swap_curves.json'
BOND_FILE = ASSETS_DIR / 'CH1493992296.json'


def build_equity_paths(s0, drift, dividend_yield, volatility, maturity_time, time_steps, num_paths, seed):
    rng = np.random.default_rng(seed)
    dt = maturity_time / time_steps
    drift_term = (drift - dividend_yield - 0.5 * volatility * volatility) * dt
    vol_term = volatility * math.sqrt(dt)

    increments = rng.standard_normal(size=(num_paths, time_steps))
    log_paths = np.cumsum(drift_term + vol_term * increments, axis=1)
    log_paths = np.hstack((np.zeros((num_paths, 1)), log_paths))
    paths = s0 * np.exp(log_paths)
    times = np.linspace(0.0, maturity_time, time_steps + 1)
    return times, paths


def get_cashflows(bond_data, evaluation_date):
    cashflows = []
    schedule = bond_data.get('coupon_schedule', [])
    for entry in schedule:
        date_str = entry.get('date')
        if not date_str:
            continue
        pay_date = parse_date(date_str)
        if pay_date <= evaluation_date:
            continue
        amount = float(entry.get('amount', 0.0))
        cashflows.append({'date': pay_date, 'amount': amount})
    return cashflows


def get_accrued_amount(bond_data, cashflows, evaluation_date):
    if not cashflows:
        return 0.0
    settlement_days = int(bond_data.get('settlement_days', 2))
    calendar_name = bond_data.get('calendar', 'TARGET')
    settlement_date = hullwhite.get_calendar(calendar_name).advance(evaluation_date, settlement_days, ql.Days)
    day_count = ql.Actual365Fixed()
    accrued = 0.0
    for i in range(len(cashflows)):
        prev_date = cashflows[i - 1]['date'] if i > 0 else evaluation_date
        next_date = cashflows[i]['date']
        if prev_date <= settlement_date < next_date:
            accrual = day_count.yearFraction(prev_date, settlement_date)
            coupon_amount = cashflows[i]['amount']
            accrued = coupon_amount * accrual
            break
    return accrued


def get_barrier_range(bond_data, evaluation_date, maturity_date):
    period = bond_data.get('barrier_observation_period', {})
    if not period:
        return 0.0, ql.Actual365Fixed().yearFraction(evaluation_date, maturity_date)

    start = parse_date(period.get('start')) if period.get('start') else evaluation_date
    end = parse_date(period.get('end')) if period.get('end') else maturity_date
    start_time = max(0.0, ql.Actual365Fixed().yearFraction(evaluation_date, start))
    end_time = min(ql.Actual365Fixed().yearFraction(evaluation_date, end), ql.Actual365Fixed().yearFraction(evaluation_date, maturity_date))
    return start_time, end_time


def get_redemption_value(path, times, barrier_level, strike_level, denomination, conversion_ratio):
    barrier_index = np.where((times >= 0.0) & (times <= times[-1]))[0]
    barrier_hit = np.min(path[barrier_index]) <= barrier_level
    final_price = float(path[-1])
    if barrier_hit and final_price <= strike_level:
        return conversion_ratio * final_price
    return denomination


def discount_date(curve_handle, ql_date):
    return float(curve_handle.discount(ql_date))


def price_barrier_convertible(curve_json, bond_data, issuer_spread_bp=None):
    evaluation_date = parse_date(bond_data.get('evaluation_date', today_date_string()))
    ql.Settings.instance().evaluationDate = evaluation_date

    discount_curve_cfg = hullwhite.select_discount_curve_config(curve_json, bond_data)
    discount_curve = hullwhite.build_discount_curve(discount_curve_cfg, evaluation_date)

    if issuer_spread_bp is None:
        issuer_spread_bp = float(bond_data.get('issuer_spread_bp', bond_data.get('credit_spread_bp', 0.0)))
    if issuer_spread_bp != 0.0:
        discount_curve = hullwhite.build_spreaded_curve(discount_curve, issuer_spread_bp)

    underlying = bond_data.get('underlying', {})
    s0 = float(underlying.get('initial_fixing_level', 0.0))
    if s0 <= 0:
        raise ValueError('underlying.initial_fixing_level is required and must be positive')

    volatility = normalize_rate(underlying.get('volatility', 0.30))
    dividend_yield = normalize_rate(underlying.get('dividend_yield', 0.0))
    drift = normalize_rate(underlying.get('drift', 0.0))

    barrier_level = float(underlying.get('barrier_level') or 0.0)
    if barrier_level <= 0:
        barrier_pct = normalize_rate(underlying.get('barrier_level_pct_of_initial', 0.0))
        barrier_level = s0 * barrier_pct
    strike_level = float(underlying.get('strike_level') or 0.0)
    if strike_level <= 0:
        strike_pct = normalize_rate(underlying.get('strike_level_pct_of_initial', 0.0))
        strike_level = s0 * strike_pct
    conversion_ratio = float(underlying.get('conversion_ratio', 1.0))

    denomination = float(bond_data.get('nominal_price') or bond_data.get('face_value') or bond_data.get('denomination') or 1000.0)
    time_steps = int(bond_data.get('mc_time_steps', 360))
    num_paths = int(bond_data.get('mc_num_paths', 5000))
    seed = int(bond_data.get('mc_seed', 42))

    maturity_date = parse_date(bond_data.get('maturity_date') or bond_data.get('end_date') or today_date_string())
    maturity_time = ql.Actual365Fixed().yearFraction(evaluation_date, maturity_date)
    if maturity_time <= 0:
        raise ValueError('maturity_date must be after evaluation_date')

    cashflows = get_cashflows(bond_data, evaluation_date)
    coupon_pv = 0.0
    for cf in cashflows:
        df = discount_date(discount_curve, cf['date'])
        coupon_pv += cf['amount'] * df

    settlement_days = int(bond_data.get('settlement_days', 2))
    settlement_date = hullwhite.get_calendar(bond_data.get('calendar', 'TARGET')).advance(evaluation_date, settlement_days, ql.Days)
    accrued_amount = get_accrued_amount(bond_data, cashflows, evaluation_date)

    barrier_start, barrier_end = get_barrier_range(bond_data, evaluation_date, maturity_date)
    times, paths = build_equity_paths(s0, drift, dividend_yield, volatility, maturity_time, time_steps, num_paths, seed)
    barrier_indices = np.where((times >= barrier_start) & (times <= barrier_end))[0]

    redemption_values = []
    for p in range(num_paths):
        path = paths[p]
        if barrier_indices.size > 0:
            barrier_path = path[barrier_indices]
        else:
            barrier_path = path
        hit = np.min(barrier_path) <= barrier_level
        final_price = float(path[-1])
        if hit and final_price <= strike_level:
            redemption_values.append(conversion_ratio * final_price)
        else:
            redemption_values.append(denomination)

    df_maturity = discount_date(discount_curve, maturity_date)
    expected_redemption = float(np.mean(redemption_values)) * df_maturity
    npv = expected_redemption + coupon_pv

    # Compute pct values relative to denomination for hullwhite compatibility
    price_pct_val = npv / denomination * 100.0 if denomination > 0 else 0.0

    return {
        'selected_npv': npv,
        'npv': npv,
        'clean_price': npv - accrued_amount,
        'dirty_price': npv,
        'accrued_amount': accrued_amount,
        'issuer_spread_bp': issuer_spread_bp,
        'volatility': volatility,
        'dividend_yield': dividend_yield,
        'drift': drift,
        'mc_time_steps': time_steps,
        'mc_num_paths': num_paths,
        'mc_seed': seed,
        'evaluation_date': evaluation_date.ISO(),
        'maturity_date': maturity_date.ISO(),
        'barrier_level': barrier_level,
        'strike_level': strike_level,
        'conversion_ratio': conversion_ratio,
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
    print(f"Volatility: {result['volatility']:.4f}")
    print(f"Dividend yield: {result['dividend_yield']:.4f}")
    print(f"Drift: {result['drift']:.4f}")
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
    bond_data = hullwhite.load_json(Path(args.bond_file))
    curve_json = hullwhite.load_json(Path(args.curve_file))
    bond_data = apply_mc_overrides(bond_data, args)
    result = price_barrier_convertible(curve_json, bond_data, issuer_spread_bp=args.issuer_spread_bp)
    print_result(bond_data, result)


if __name__ == '__main__':
    main()
