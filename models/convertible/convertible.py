"""Common helpers shared by all convertible / reverse-convertible pricers.

This module is not a pricer itself — it provides the building blocks used by
barrier_convertible, barrier_discount, simple_convertible, and
autocallable_reverse_convertible.

Exported utilities
------------------
  build_equity_paths(...)              Single-underlying GBM path generation
  build_correlated_equity_paths(...)   Multi-underlying GBM with Cholesky correlation
  get_cashflows(bond_data, eval_date)  Reads coupon_schedule into a list of (date, amount)
  get_accrued_amount(...)              Accrued coupon at settlement
  discount_date(curve_handle, ql_date) Discount factor from the curve handle
  build_ul_meta(bond_data, ul_list)    Builds (ul_meta, ul_params) from underlyings array
  build_corr_matrix(bond_data, N)      Parses correlation matrix or returns None
  resolve_underlyings(bond_data)       Returns the underlyings list from bond_data
  worst_of_redemption(...)             Reverse convertible payoff at maturity
  standard_convertible_redemption(...) Standard convertible payoff at maturity
"""

import math

import numpy as np
import QuantLib as ql

try:
    from models.helper import normalize_rate, parse_date, get_calendar
except (ModuleNotFoundError, ImportError):
    from helper import normalize_rate, parse_date, get_calendar


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


def build_correlated_equity_paths(ul_params, maturity_time, time_steps, num_paths, seed, corr_matrix=None):
    """Generate MC paths for N underlyings with optional correlation via Cholesky decomposition.

    ul_params: list of (s0, drift, dividend_yield, volatility) per underlying
    corr_matrix: NxN numpy array, or None for independent paths
    Returns: (times, [paths_0, paths_1, ...])
    """
    N = len(ul_params)
    rng = np.random.default_rng(seed)
    dt = maturity_time / time_steps
    times = np.linspace(0.0, maturity_time, time_steps + 1)

    z = rng.standard_normal(size=(num_paths, time_steps, N))

    if corr_matrix is not None and N > 1:
        try:
            L = np.linalg.cholesky(corr_matrix)
            z = z @ L.T
        except np.linalg.LinAlgError:
            pass  # not positive-definite — fall back to independent paths

    all_paths = []
    for j, (s0, drift, div_yield, vol) in enumerate(ul_params):
        drift_term = (drift - div_yield - 0.5 * vol * vol) * dt
        vol_term = vol * math.sqrt(dt)
        increments = z[:, :, j]
        log_paths = np.cumsum(drift_term + vol_term * increments, axis=1)
        log_paths = np.hstack((np.zeros((num_paths, 1)), log_paths))
        all_paths.append(s0 * np.exp(log_paths))

    return times, all_paths


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
    settlement_date = get_calendar(calendar_name).advance(evaluation_date, settlement_days, ql.Days)
    day_count = ql.Actual365Fixed()
    accrued = 0.0
    for i in range(len(cashflows)):
        prev_date = cashflows[i - 1]['date'] if i > 0 else evaluation_date
        next_date = cashflows[i]['date']
        if prev_date <= settlement_date < next_date:
            accrual = day_count.yearFraction(prev_date, settlement_date)
            accrued = cashflows[i]['amount'] * accrual
            break
    return accrued


def discount_date(curve_handle, ql_date):
    return float(curve_handle.discount(ql_date))


def _get_ul_barrier(ul, s0):
    barrier = float(ul.get('barrier_level') or 0.0)
    if barrier <= 0:
        barrier = s0 * normalize_rate(ul.get('barrier_level_pct_of_initial', 0.0))
    return barrier


def _get_ul_strike(ul, s0):
    strike = float(ul.get('strike_level') or 0.0)
    if strike <= 0:
        strike = s0 * normalize_rate(ul.get('strike_level_pct_of_initial', 0.0))
    return strike


def build_ul_meta(bond_data, underlyings_list):
    """Parse per-underlying parameters from bond_data.

    Returns:
        ul_meta: list of (ul_dict, s0, barrier, strike, conv_ratio, vol)
        ul_params: list of (s0, drift, div_yield, vol) for MC path builder
    """
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
        drift_i   = normalize_rate(ul.get('drift', 0.0))
        barrier_i = _get_ul_barrier(ul, s0)
        strike_i  = _get_ul_strike(ul, s0)
        conv_i    = float(ul.get('conversion_ratio', 1.0))
        ul_meta.append((ul, s0, barrier_i, strike_i, conv_i, vol))
        ul_params.append((s0, drift_i, div_yield, vol))
    return ul_meta, ul_params


def build_corr_matrix(bond_data, N):
    """Build correlation matrix from bond_data fields, or None for independent."""
    raw_corr = bond_data.get('correlation_matrix')
    if raw_corr is not None:
        return np.array(raw_corr, dtype=float)
    scalar_corr = bond_data.get('underlying_correlation')
    if scalar_corr is not None and N > 1:
        rho = float(scalar_corr)
        corr = np.full((N, N), rho)
        np.fill_diagonal(corr, 1.0)
        return corr
    return None


def resolve_underlyings(bond_data):
    """Return the list of underlying dicts from bond_data, raising if empty."""
    underlyings_list = bond_data.get('underlyings') or []
    if not underlyings_list:
        single = bond_data.get('underlying') or {}
        if single and float(single.get('initial_fixing_level', 0.0)) > 0:
            underlyings_list = [single]
    if not underlyings_list:
        raise ValueError('No underlyings with initial_fixing_level found in bond_data')
    return underlyings_list


def _worst_underlying(ul_paths, path_idx):
    """Return (ul, s0, barrier, strike, conv, paths, vol) for the worst performer at path_idx."""
    worst_perf = None
    worst_idx = 0
    for idx, (_, s0_i, _, _, _, paths_i, _vol) in enumerate(ul_paths):
        perf = paths_i[path_idx][-1] / s0_i if s0_i > 0 else 0.0
        if worst_perf is None or perf < worst_perf:
            worst_perf = perf
            worst_idx = idx
    return ul_paths[worst_idx]


def worst_of_redemption(ul_paths, path_idx, denomination):
    """Reverse convertible payoff at maturity (no barrier check).

    Investor receives denomination unless the worst-of underlying finishes
    below its strike, in which case physical delivery applies.
    """
    _, _, _, strike_w, conv_w, paths_w, _ = _worst_underlying(ul_paths, path_idx)
    final_w = float(paths_w[path_idx][-1])
    if final_w <= strike_w:
        return conv_w * final_w
    return denomination


def standard_convertible_redemption(ul_paths, path_idx, denomination):
    """Standard convertible payoff at maturity.

    Investor holds a long call: if the worst-of underlying finishes above
    its conversion price (strike) the investor converts to shares; otherwise
    receives denomination (bond floor).

    Redemption = max(denomination, conv_ratio × final_price)
    """
    _, _, _, strike_w, conv_w, paths_w, _ = _worst_underlying(ul_paths, path_idx)
    final_w = float(paths_w[path_idx][-1])
    return max(denomination, conv_w * final_w)
