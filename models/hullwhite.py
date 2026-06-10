import argparse
from datetime import date
import json
import math
from pathlib import Path

import QuantLib as ql

BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent
ASSETS_DIR = PROJECT_ROOT / 'assets'
CURVES_DIR = PROJECT_ROOT / 'curves'
CURVE_FILE = CURVES_DIR / 'swap_curves.json'
BOND_FILE = ASSETS_DIR / 'XS1693822634.json'


def today_date_string():
    return date.today().strftime('%d-%m-%Y')


def apply_runtime_pricing_defaults(data):
    if isinstance(data, dict) and data.get('instrument_id'):
        data = dict(data)
        data['evaluation_date'] = today_date_string()
    return data


def resolve_json_path(path: Path):
    if path.is_absolute():
        return path

    candidates = [
        path,
        PROJECT_ROOT / path,
        ASSETS_DIR / path,
        CURVES_DIR / path,
    ]

    for candidate in candidates:
        if candidate.exists():
            return candidate

    # If a bare filename is provided, prefer assets for bond JSON and curves for curve JSON.
    if path.parent == Path('.'):
        asset_candidate = ASSETS_DIR / path.name
        if asset_candidate.exists():
            return asset_candidate
        curve_candidate = CURVES_DIR / path.name
        if curve_candidate.exists():
            return curve_candidate

    return path


def load_json(path: Path):
    path = resolve_json_path(path)
    with open(path, 'r', encoding='utf-8-sig') as f:
        content = f.read().strip()

    if not content:
        raise ValueError(f'JSON file is empty: {path}')

    try:
        return apply_runtime_pricing_defaults(json.loads(content))
    except json.JSONDecodeError as exc:
        raise ValueError(f'Invalid JSON in {path}: {exc}') from exc


def parse_date(date_str: str):
    day, month, year = map(int, date_str.split('-'))
    return ql.Date(day, month, year)


def get_calendar(name: str):
    calendars = {
        'TARGET': ql.TARGET,
        'UnitedStates': lambda: ql.UnitedStates(ql.UnitedStates.GovernmentBond),
        'TARGET+UnitedStates': ql.TARGET,
    }
    if name not in calendars:
        raise ValueError(f'Unsupported calendar: {name}')
    return calendars[name]()


def get_day_count(name: str):
    day_counts = {
        'Actual365Fixed': ql.Actual365Fixed,
        'Actual360': ql.Actual360,
        'Thirty360': lambda: ql.Thirty360(ql.Thirty360.BondBasis),
        '30/360': lambda: ql.Thirty360(ql.Thirty360.BondBasis),
    }
    if name not in day_counts:
        raise ValueError(f'Unsupported day count: {name}')
    return day_counts[name]()


def get_business_day_convention(name: str):
    conventions = {
        'Unadjusted': ql.Unadjusted,
        'ModifiedFollowing': ql.ModifiedFollowing,
        'Following': ql.Following,
    }
    if name not in conventions:
        raise ValueError(f'Unsupported business day convention: {name}')
    return conventions[name]


def get_frequency(name: str):
    frequencies = {
        'Annual': ql.Annual,
        'Semiannual': ql.Semiannual,
        'Quarterly': ql.Quarterly,
        'Monthly': ql.Monthly,
    }
    if name not in frequencies:
        raise ValueError(f'Unsupported coupon frequency: {name}')
    return frequencies[name]


def get_date_generation(name: str):
    rules = {
        'Forward': ql.DateGeneration.Forward,
        'Backward': ql.DateGeneration.Backward,
    }
    if name not in rules:
        raise ValueError(f'Unsupported date generation rule: {name}')
    return rules[name]


def get_reference_day_count(name: str):
    ref_day_counts = {
        'Actual360': ql.Actual360,
        'Actual365Fixed': ql.Actual365Fixed,
    }
    if name not in ref_day_counts:
        raise ValueError(f'Unsupported reference rate day count: {name}')
    return ref_day_counts[name]()


def normalize_curve_catalog(curve_json):
    if isinstance(curve_json, dict):
        return None
    if not isinstance(curve_json, list):
        raise ValueError('Curve file must be a single curve object or a list of curve objects.')

    catalog = {}
    for entry in curve_json:
        if not isinstance(entry, dict):
            continue
        curve_name = entry.get('curve_name')
        if not curve_name:
            continue
        catalog[curve_name] = entry
    if not catalog:
        raise ValueError('No named curves found in curve catalog JSON.')
    return catalog


def infer_currency_from_isin(isin):
    if not isin:
        return None
    prefix = str(isin).strip().upper()[:2]
    if prefix in {'US', 'XS', 'EU'}:
        if prefix == 'US':
            return 'USD'
        if prefix in {'XS', 'EU'}:
            return 'EUR'
    return None


def select_discount_curve_config(curve_json, bond_data):
    catalog = normalize_curve_catalog(curve_json)
    if catalog is None:
        return curve_json

    requested_name = bond_data.get('discount_curve_name') or bond_data.get('curve_name')
    if requested_name:
        if requested_name not in catalog:
            raise ValueError(f"Requested discount_curve_name not found: {requested_name}")
        return catalog[requested_name]

    currency = str(
        bond_data.get('currency')
        or infer_currency_from_isin(bond_data.get('instrument_id'))
        or 'EUR'
    ).upper()
    default_by_currency = {
        'EUR': 'EUR_OIS_PROXY',
        'USD': 'USD_OIS_PROXY',
    }
    default_name = default_by_currency.get(currency, 'EUR_OIS_PROXY')
    if default_name in catalog:
        return catalog[default_name]

    for name, cfg in catalog.items():
        if name.upper().startswith(f'{currency}_') and 'OIS' in name.upper() and 'pillars' in cfg:
            return cfg

    raise ValueError(f'No discount curve available for currency={currency}. Add discount_curve_name in bond JSON.')


def tenor_to_period(tenor: str):
    value = tenor.strip().upper()
    if value == 'ON':
        return ql.Period(1, ql.Days)
    if value.endswith('D'):
        return ql.Period(int(value[:-1]), ql.Days)
    if value.endswith('W'):
        return ql.Period(int(value[:-1]), ql.Weeks)
    if value.endswith('M'):
        return ql.Period(int(value[:-1]), ql.Months)
    if value.endswith('Y'):
        return ql.Period(int(value[:-1]), ql.Years)
    raise ValueError(f'Unsupported tenor format: {tenor}')


def tenor_to_years(tenor: str):
    value = tenor.strip().upper()
    if value == 'ON':
        return 1.0 / 365.0
    if value.endswith('D'):
        return float(value[:-1]) / 365.0
    if value.endswith('W'):
        return float(value[:-1]) * 7.0 / 365.0
    if value.endswith('M'):
        return float(value[:-1]) / 12.0
    if value.endswith('Y'):
        return float(value[:-1])
    raise ValueError(f'Unsupported tenor format: {tenor}')


def select_named_curve_config(curve_json, curve_name):
    if not curve_name:
        return None
    catalog = normalize_curve_catalog(curve_json)
    if catalog is None:
        raise ValueError('Named curve selection requires a curve catalog JSON list.')
    if curve_name not in catalog:
        raise ValueError(f'Requested curve not found in catalog: {curve_name}')
    return catalog[curve_name]


def interpolate_surface_vol(surface_cfg, target_expiry_years, target_tenor_years):
    quotes = surface_cfg.get('quotes', [])
    if not quotes:
        return 0.0

    parsed = []
    for q in quotes:
        expiry = tenor_to_years(q['expiry'])
        tenor = tenor_to_years(q['tenor'])
        vol = float(q['vol'])
        parsed.append((expiry, tenor, vol))

    if not parsed:
        return 0.0

    tenors = sorted(set(t for _, t, _ in parsed))
    nearest_tenor = min(tenors, key=lambda x: abs(x - target_tenor_years))
    slice_points = sorted((e, v) for e, t, v in parsed if t == nearest_tenor)
    if not slice_points:
        return 0.0

    if target_expiry_years <= slice_points[0][0]:
        return slice_points[0][1]
    if target_expiry_years >= slice_points[-1][0]:
        return slice_points[-1][1]

    for i in range(1, len(slice_points)):
        e0, v0 = slice_points[i - 1]
        e1, v1 = slice_points[i]
        if e0 <= target_expiry_years <= e1:
            if e1 == e0:
                return v1
            w = (target_expiry_years - e0) / (e1 - e0)
            return v0 + w * (v1 - v0)
    return slice_points[-1][1]


def build_cms_context(curve_json, bond_data, eval_date, default_curve):
    if bond_data.get('coupon_structure', 'fixed') != 'cms_resettable':
        return None

    cms_curve = default_curve
    cms_curve_name = bond_data.get('cms_swap_curve_name')
    if cms_curve_name:
        cms_curve_cfg = select_named_curve_config(curve_json, cms_curve_name)
        if 'pillars' not in cms_curve_cfg:
            raise ValueError(f'CMS curve {cms_curve_name} has no pillars.')
        cms_curve = build_discount_curve(cms_curve_cfg, eval_date)

    cms_vol_cfg = bond_data.get('cms_vol_adjustment', {})
    vol_surface_cfg = None
    if cms_vol_cfg.get('enabled', False):
        vol_curve_name = cms_vol_cfg.get('vol_surface_name')
        if not vol_curve_name:
            raise ValueError('cms_vol_adjustment.enabled=true requires vol_surface_name.')
        vol_surface_cfg = select_named_curve_config(curve_json, vol_curve_name)
        if 'quotes' not in vol_surface_cfg:
            raise ValueError(f'Vol surface {vol_curve_name} is missing quotes.')

    return {
        'cms_curve': cms_curve,
        'vol_surface_cfg': vol_surface_cfg,
    }


def get_coupon_rate(curve, d0, d1, bond_data, eval_date, cms_context=None):
    structure = bond_data.get('coupon_structure', 'fixed')

    if structure == 'fixed':
        return bond_data['fixed_coupon_rate']

    if structure == 'cms_resettable':
        cms_curve = curve
        vol_surface_cfg = None
        if cms_context is not None:
            cms_curve = cms_context.get('cms_curve', curve)
            vol_surface_cfg = cms_context.get('vol_surface_cfg')

        calendar = get_calendar(bond_data['calendar'])
        business_day_convention = get_business_day_convention(bond_data['business_day_convention'])
        cms_tenor_years = int(bond_data.get('cms_tenor_years', 10))
        cms_day_count = get_reference_day_count(
            bond_data.get('cms_day_count', bond_data.get('accrual_day_count', 'Actual360'))
        )
        cms_fixed_leg_frequency = get_frequency(
            bond_data.get('cms_fixed_leg_frequency', 'Annual')
        )

        reset_date = d0 if d0 > eval_date else eval_date
        swap_end = calendar.advance(reset_date, ql.Period(cms_tenor_years, ql.Years), business_day_convention)
        fixed_schedule = ql.Schedule(
            reset_date,
            swap_end,
            ql.Period(cms_fixed_leg_frequency),
            calendar,
            business_day_convention,
            business_day_convention,
            ql.DateGeneration.Forward,
            False,
        )

        annuity = 0.0
        for i in range(1, len(fixed_schedule)):
            f0 = fixed_schedule[i - 1]
            f1 = fixed_schedule[i]
            alpha = cms_day_count.yearFraction(f0, f1)
            annuity += alpha * cms_curve.discount(f1)

        if annuity <= 0.0:
            cms_rate = 0.0
        else:
            cms_rate = (cms_curve.discount(reset_date) - cms_curve.discount(swap_end)) / annuity

        cms_vol_cfg = bond_data.get('cms_vol_adjustment', {})
        if cms_vol_cfg.get('enabled', False) and vol_surface_cfg is not None:
            expiry_years = max(1.0 / 12.0, ql.Actual365Fixed().yearFraction(eval_date, reset_date))
            swap_tenor_years = float(cms_tenor_years)
            atm_vol = interpolate_surface_vol(vol_surface_cfg, expiry_years, swap_tenor_years)
            convexity_lambda = float(cms_vol_cfg.get('convexity_lambda', 1.0))
            callability_lambda = float(cms_vol_cfg.get('callability_lambda', 0.0))

            convexity_adj = 0.5 * convexity_lambda * (atm_vol ** 2) * expiry_years
            callability_adj = 0.0

            call_dates = [parse_date(x) for x in bond_data.get('call_dates', [])]
            future_calls = [x for x in call_dates if x >= reset_date]
            if future_calls and callability_lambda > 0.0:
                next_call = min(future_calls)
                tau_call = max(0.0, ql.Actual365Fixed().yearFraction(reset_date, next_call))
                callability_adj = callability_lambda * atm_vol * math.sqrt(tau_call)

            cms_rate = cms_rate + convexity_adj - callability_adj

        rate = bond_data.get('cms_multiplier', 1.0) * cms_rate + bond_data.get('cms_spread', 0.0)
        if 'cms_floor' in bond_data:
            rate = max(rate, bond_data['cms_floor'])
        if 'cms_cap' in bond_data:
            rate = min(rate, bond_data['cms_cap'])
        return rate

    if structure != 'fixed_to_float':
        raise ValueError(f'Unsupported coupon_structure: {structure}')

    fixed_end_date = parse_date(bond_data['fixed_rate_end_date'])
    if d1 <= fixed_end_date:
        return bond_data['fixed_coupon_rate']

    ref_day_count = get_reference_day_count(
        bond_data.get('float_reference_day_count', 'Actual360')
    )
    spread = bond_data.get('float_spread', 0.0)
    floor_rate = bond_data.get('float_floor')

    d_start = d0 if d0 > eval_date else eval_date
    if d_start >= d1:
        fwd_rate = 0.0
    else:
        yf = ref_day_count.yearFraction(d_start, d1)
        if yf <= 0.0:
            fwd_rate = 0.0
        else:
            df0 = curve.discount(d_start)
            df1 = curve.discount(d1)
            fwd_rate = (df0 / df1 - 1.0) / yf

    rate = fwd_rate + spread
    if floor_rate is not None:
        rate = max(rate, floor_rate)
    return rate


def build_discount_curve(curve_json, evaluation_date):
    calendar = get_calendar(curve_json.get('calendar', 'TARGET'))
    ql.Settings.instance().evaluationDate = evaluation_date
    day_count = get_day_count(curve_json.get('day_count', 'Actual365Fixed'))

    pillars = curve_json.get('pillars', [])
    if not pillars:
        raise ValueError('Selected curve has no pillars.')

    date_rate_pairs = []
    for p in pillars:
        period = tenor_to_period(p['tenor'])
        pillar_date = calendar.advance(evaluation_date, period, ql.Following)
        date_rate_pairs.append((pillar_date, float(p['rate'])))

    date_rate_pairs.sort(key=lambda x: int(x[0].serialNumber()))
    unique_dates = {}
    for d, r in date_rate_pairs:
        unique_dates[int(d.serialNumber())] = (d, r)

    sorted_pairs = [unique_dates[k] for k in sorted(unique_dates.keys())]
    first_rate = sorted_pairs[0][1]
    dates = [evaluation_date]
    rates = [first_rate]
    for d, r in sorted_pairs:
        if d == evaluation_date:
            rates[0] = r
            continue
        dates.append(d)
        rates.append(r)

    if len(dates) < 2:
        raise ValueError('Insufficient curve pillars to build term structure.')

    curve = ql.ZeroCurve(dates, rates, day_count, calendar)
    curve.enableExtrapolation()
    return curve


def build_coupon_schedule(bond_data):
    issue_date = parse_date(bond_data['issue_date'])
    if 'maturity_date' in bond_data:
        maturity_date = parse_date(bond_data['maturity_date'])
    elif 'end_date' in bond_data:
        maturity_date = parse_date(bond_data['end_date'])
    else:
        raise ValueError('Bond JSON must include either maturity_date or end_date')
    calendar = get_calendar(bond_data['calendar'])
    business_day_convention = get_business_day_convention(bond_data['business_day_convention'])
    date_generation_rule = get_date_generation(bond_data['date_generation'])

    structure = bond_data.get('coupon_structure', 'fixed')
    if structure == 'fixed_to_float':
        frequency_name = bond_data.get('float_coupon_frequency', bond_data['coupon_frequency'])
    elif structure == 'cms_resettable':
        frequency_name = bond_data.get('cms_coupon_frequency', bond_data['coupon_frequency'])
    else:
        frequency_name = bond_data['coupon_frequency']

    frequency = get_frequency(frequency_name)
    schedule = ql.Schedule(
        issue_date,
        maturity_date,
        ql.Period(frequency),
        calendar,
        business_day_convention,
        business_day_convention,
        date_generation_rule,
        False,
    )
    return schedule, maturity_date


def price_to_call_date(curve, bond_data, call_date, schedule, cms_context=None):
    eval_date = ql.Settings.instance().evaluationDate
    day_count = get_day_count(bond_data['accrual_day_count'])
    par = bond_data['par']
    spread_bp = bond_data['credit_spread_bp']
    spread = spread_bp / 10000.0

    pv = 0.0
    cashflows = []
    for i in range(1, len(schedule)):
        d0 = schedule[i - 1]
        d1 = schedule[i]
        if d1 > call_date:
            break

        accrual = day_count.yearFraction(d0, d1)
        coupon_rate = get_coupon_rate(curve, d0, d1, bond_data, eval_date, cms_context=cms_context)
        cf = par * coupon_rate * accrual
        t = day_count.yearFraction(eval_date, d1)
        if t < 0:
            continue

        df = curve.discount(d1) * math.exp(-spread * t)
        pv_cf = cf * df
        pv += pv_cf
        cashflows.append((d1.ISO(), coupon_rate, cf, df, pv_cf))

    t_call = day_count.yearFraction(eval_date, call_date)
    df_call = curve.discount(call_date) * math.exp(-spread * t_call)
    redemption = par * df_call
    pv += redemption

    return pv, redemption, cashflows


def price_bond(curve, bond_data, curve_json=None):
    schedule, maturity_date = build_coupon_schedule(bond_data)
    eval_date = ql.Settings.instance().evaluationDate
    spread_bp = bond_data['credit_spread_bp']
    cms_context = None
    if curve_json is not None:
        cms_context = build_cms_context(curve_json, bond_data, eval_date, curve)
    raw_call_dates = bond_data.get('call_dates', [])

    if raw_call_dates:
        call_dates = [parse_date(d) for d in raw_call_dates]
    else:
        if 'end_date' in bond_data:
            call_dates = [parse_date(bond_data['end_date'])]
        else:
            call_dates = [maturity_date]

    eligible_call_dates = sorted(
        d for d in call_dates if d >= eval_date and d <= maturity_date
    )
    if not eligible_call_dates:
        eligible_call_dates = [maturity_date]

    scenarios = []
    for call_date in eligible_call_dates:
        npv, redemption, cashflows = price_to_call_date(curve, bond_data, call_date, schedule, cms_context=cms_context)
        scenarios.append(
            {
                'call_date': call_date.ISO(),
                'npv': npv,
                'redemption_pv': redemption,
                'cashflows': cashflows,
            }
        )

    maturity_npv, maturity_redemption, maturity_cashflows = price_to_call_date(
        curve, bond_data, maturity_date, schedule, cms_context=cms_context
    )
    maturity_scenario = {
        'call_date': maturity_date.ISO(),
        'npv': maturity_npv,
        'redemption_pv': maturity_redemption,
        'cashflows': maturity_cashflows,
    }

    worst = min(scenarios, key=lambda x: x['npv'])
    first = min(scenarios, key=lambda x: x['call_date'])

    # Select which NPV concept to report for callable structures.
    if 'valuation_mode' in bond_data:
        valuation_mode = bond_data['valuation_mode']
    else:
        valuation_mode = 'to_maturity'

    if valuation_mode == 'worst_call':
        selected = worst
    elif valuation_mode == 'first_call':
        selected = first
    elif valuation_mode == 'to_maturity':
        selected = maturity_scenario
    else:
        raise ValueError(f'Unsupported valuation_mode: {valuation_mode}')

    return {
        'selected_npv': selected['npv'],
        'valuation_mode': valuation_mode,
        'selected_call_date': selected['call_date'],
        'redemption_pv': selected['redemption_pv'],
        'spread_bp': spread_bp,
        'cashflows': selected['cashflows'],
        'npv_to_worst_call': worst['npv'],
        'npv_to_first_call': first['npv'],
        'npv_to_maturity': maturity_scenario['npv'],
        'scenarios': scenarios,
    }


def get_model_price(result):
    return result['selected_npv']


def get_par_amount(bond_data):
    return float(bond_data.get('par', 100.0))


def get_issue_price_pct(bond_data):
    return float(bond_data.get('issue_price', 100.0))


def amount_to_pct(value, bond_data):
    par_amount = get_par_amount(bond_data)
    return value * 100.0 / par_amount


def pct_to_amount(value_pct, bond_data):
    par_amount = get_par_amount(bond_data)
    return value_pct * par_amount / 100.0


def implied_spread_bp(curve, bond_data, market_price, curve_json=None, low_bp=-500.0, high_bp=3000.0, tol=1e-6, max_iter=120):
    def price_at(spread_bp):
        trial = dict(bond_data)
        trial['credit_spread_bp'] = spread_bp
        trial_result = price_bond(curve, trial, curve_json=curve_json)
        return get_model_price(trial_result)

    low_price = price_at(low_bp)
    high_price = price_at(high_bp)

    # Expand bracket if needed.
    expand_count = 0
    while not (low_price >= market_price >= high_price) and expand_count < 20:
        low_bp -= 500.0
        high_bp += 500.0
        low_price = price_at(low_bp)
        high_price = price_at(high_bp)
        expand_count += 1

    if not (low_price >= market_price >= high_price):
        raise ValueError('Could not bracket implied spread for the provided market_price.')

    for _ in range(max_iter):
        mid_bp = 0.5 * (low_bp + high_bp)
        mid_price = price_at(mid_bp)
        if abs(mid_price - market_price) < tol:
            return mid_bp

        if mid_price > market_price:
            low_bp = mid_bp
        else:
            high_bp = mid_bp

    return 0.5 * (low_bp + high_bp)


def price_with_spread_bp(curve, bond_data, spread_bp, curve_json=None):
    trial = dict(bond_data)
    trial['credit_spread_bp'] = spread_bp
    trial_result = price_bond(curve, trial, curve_json=curve_json)
    return get_model_price(trial_result)


def print_bond_result(bond_data, result, curve=None, curve_json=None):
    issue_price_pct = get_issue_price_pct(bond_data)
    selected_npv_pct = amount_to_pct(result['selected_npv'], bond_data)
    worst_npv_pct = amount_to_pct(result['npv_to_worst_call'], bond_data)
    first_npv_pct = amount_to_pct(result['npv_to_first_call'], bond_data)
    maturity_npv_pct = amount_to_pct(result['npv_to_maturity'], bond_data)
    redemption_pct = amount_to_pct(result['redemption_pv'], bond_data)

    print(f"{bond_data['description']} ({bond_data['instrument_id']})")
    print(f"Valuation mode: {result['valuation_mode']}")
    print(f"Selected call date: {result['selected_call_date']}")
    print(f"Issue price (%): {issue_price_pct:.4f}")
    print(f"Selected price (%): {selected_npv_pct:.6f}")
    print(f"Price (worst call) (%): {worst_npv_pct:.6f}")
    print(f"Price (first call) (%): {first_npv_pct:.6f}")
    print(f"Price (to maturity) (%): {maturity_npv_pct:.6f}")
    print(f"Redemption PV (%): {redemption_pct:.6f}")
    print(f"Spread: {result['spread_bp']:.1f} bp")
    print(f"Model - Issue (%): {selected_npv_pct - issue_price_pct:.6f}")

    if bond_data.get('coupon_structure') == 'cms_resettable':
        cms_curve_name = bond_data.get('cms_swap_curve_name', 'discount_curve_default')
        cms_vol_cfg = bond_data.get('cms_vol_adjustment', {})
        cms_vol_enabled = bool(cms_vol_cfg.get('enabled', False))
        cms_vol_name = cms_vol_cfg.get('vol_surface_name', 'none')
        print(f"CMS swap curve: {cms_curve_name}")
        print(f"CMS vol adjustment enabled: {cms_vol_enabled}")
        if cms_vol_enabled:
            print(f"CMS vol surface: {cms_vol_name}")

    if 'market_price' in bond_data and curve is not None:
        market_price = float(bond_data['market_price'])
        market_price_amount = pct_to_amount(market_price, bond_data)
        model_price = get_model_price(result)
        model_price_pct = amount_to_pct(model_price, bond_data)
        diff_pct = model_price_pct - market_price
        imp_spread = implied_spread_bp(curve, bond_data, market_price_amount, curve_json=curve_json)
        fitted_price = price_with_spread_bp(curve, bond_data, imp_spread, curve_json=curve_json)
        fitted_price_pct = amount_to_pct(fitted_price, bond_data)
        print(f"Market price (%): {market_price:.6f}")
        print(f"Model - Market (%): {diff_pct:.6f}")
        print(f"Implied spread from market price: {imp_spread:.2f} bp")
        print(f"Model price at implied spread (%): {fitted_price_pct:.6f}")
        print(f"Residual at implied spread (%): {fitted_price_pct - market_price:.8f}")

    if result.get('scenarios'):
        print('Scenarios:')
        for scenario in result['scenarios']:
            scenario_pct = amount_to_pct(scenario['npv'], bond_data)
            print(f"  {scenario['call_date']}: {scenario_pct:.6f}%")
    print()


def print_bond_skip(bond_file: Path, error):
    print(f"{bond_file.name}")
    print(f"Skipped: {error}")
    print()


def get_bond_files(base_dir: Path):
    bond_files = []
    for path in sorted(base_dir.glob('*.json')):
        if path.name == CURVE_FILE.name:
            continue
        if 'curve' in path.name.lower():
            continue
        if path.name.startswith('.'):
            continue
        bond_files.append(path)
    return bond_files


def run_all_bonds(curve_json, bond_files=None):
    if bond_files is None:
        bond_files = get_bond_files(ASSETS_DIR)

    results = []
    for bond_file in bond_files:
        try:
            bond_data = load_json(bond_file)
            evaluation_date = parse_date(bond_data['evaluation_date'])
            discount_curve_cfg = select_discount_curve_config(curve_json, bond_data)
            curve = build_discount_curve(discount_curve_cfg, evaluation_date)
            result = price_bond(curve, bond_data, curve_json=curve_json)
        except Exception as exc:
            print_bond_skip(bond_file, exc)
            continue

        model_price = get_model_price(result)
        model_price_pct = amount_to_pct(model_price, bond_data)
        market_price = bond_data.get('market_price')
        if market_price is not None:
            diff = model_price_pct - float(market_price)
        else:
            diff = None

        results.append(
            {
                'bond_file': bond_file.name,
                'instrument_id': bond_data['instrument_id'],
                'description': bond_data.get('description', bond_data['instrument_id']),
                'valuation_mode': result['valuation_mode'],
                'selected_call_date': result['selected_call_date'],
                'model_price': model_price_pct,
                'market_price': market_price,
                'model_minus_market': diff,
                'implied_spread_bp': (
                    implied_spread_bp(curve, bond_data, pct_to_amount(float(market_price), bond_data), curve_json=curve_json)
                    if market_price is not None
                    else None
                ),
            }
        )

    return results


def parse_args():
    parser = argparse.ArgumentParser(description='Price a bond from JSON terms using a simplified QuantLib setup.')
    parser.add_argument(
        '--all-bonds',
        action='store_true',
        help='Price all known bond JSON files in the project folder',
    )
    parser.add_argument(
        '--bond-file',
        default=str(BOND_FILE),
        help='Path to bond JSON input file',
    )
    parser.add_argument(
        '--curve-file',
        default=str(CURVE_FILE),
        help='Path to swap curve JSON input file (single curve or catalog)',
    )
    return parser.parse_args()


if __name__ == '__main__':
    args = parse_args()
    curve_json = load_json(Path(args.curve_file))

    if args.all_bonds:
        bond_files = get_bond_files(ASSETS_DIR)
        for bond_file in bond_files:
            try:
                bond_data = load_json(bond_file)
                evaluation_date = parse_date(bond_data['evaluation_date'])
                discount_curve_cfg = select_discount_curve_config(curve_json, bond_data)
                curve = build_discount_curve(discount_curve_cfg, evaluation_date)
                result = price_bond(curve, bond_data, curve_json=curve_json)
            except Exception as exc:
                print_bond_skip(bond_file, exc)
                continue
            print_bond_result(bond_data, result, curve, curve_json=curve_json)
        raise SystemExit(0)

    bond_data = load_json(Path(args.bond_file))
    evaluation_date = parse_date(bond_data['evaluation_date'])
    discount_curve_cfg = select_discount_curve_config(curve_json, bond_data)
    curve = build_discount_curve(discount_curve_cfg, evaluation_date)
    result = price_bond(curve, bond_data, curve_json=curve_json)
    print_bond_result(bond_data, result, curve, curve_json=curve_json)
