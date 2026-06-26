"""
Shared utility functions for pricing models.

Avoids duplicating simple helpers like
- today_date_string
- normalize_rate
- parse_date (QuantLib version)
across the models/ folder.
"""

from datetime import date
import json
import math
from pathlib import Path
import QuantLib as ql

BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent
ASSETS_DIR = PROJECT_ROOT / 'assets'
CURVES_DIR = PROJECT_ROOT / 'curves'

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


def apply_runtime_pricing_defaults(data):
    if isinstance(data, dict) and data.get('instrument_id'):
        data = dict(data)
        data['evaluation_date'] = today_date_string()
    return data


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
        'ActualActual': lambda: ql.ActualActual(ql.ActualActual.ISDA),
        'ACT/ACT': lambda: ql.ActualActual(ql.ActualActual.ISDA),
        'ACT/ACT (PERIODIC BASIS)': lambda: ql.ActualActual(ql.ActualActual.ISDA),
        'ACT/ACT (ICMA)': lambda: ql.ActualActual(ql.ActualActual.ISDA),
    }
    if name not in day_counts:
        raise ValueError(f'Unsupported day count: {name}')
    return day_counts[name]()



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


def today_date_string() -> str:
    """Return today's date as DD-MM-YYYY (consistent with hullwhite.parse_date)."""
    return date.today().strftime('%d-%m-%Y')


def today_date_string_iso() -> str:
    """Return today's date as YYYY-MM-DD (ISO 8601)."""
    return date.today().strftime('%Y-%m-%d')


def normalize_rate(value, default=0.0):
    """Convert a percentage value to a decimal rate.

    - ``None`` / empty → *default*
    - If ``abs(value) > 1.0``, divides by 100.
    """
    if value is None or value == '':
        return float(default)
    value = float(value)
    return value / 100.0 if abs(value) > 1.0 else value


def parse_date(date_str: str) -> ql.Date:
    """Parse a DD-MM-YYYY or YYYY-MM-DD string and return a QuantLib Date.

    If *date_str* is falsy, today is used (via ``today_date_string``).
    """
    if not date_str:
        return parse_date(today_date_string())  # recursion safe: today_date_string always valid
    parts = date_str.strip().split('-')
    if len(parts) != 3:
        raise ValueError(f'Unsupported date format: {date_str}')
    if len(parts[0]) == 4:           # YYYY-MM-DD
        year, month, day = parts
    else:                             # DD-MM-YYYY
        day, month, year = parts
    return ql.Date(int(day), int(month), int(year))


def tenor_to_period(tenor: str) -> ql.Period:
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


def tenor_to_years(tenor: str) -> float:
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


def infer_currency_from_isin(isin: str):
    if not isin:
        return None
    prefix = str(isin).strip().upper()[:2]
    if prefix == 'US':
        return 'USD'
    if prefix in {'XS', 'EU'}:
        return 'EUR'
    return None


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


def select_discount_curve_config(curve_json, bond_data):
    catalog = normalize_curve_catalog(curve_json)
    if catalog is None:
        return curve_json

    instr = str(bond_data.get('instrument_id') or bond_data.get('isin') or '').strip()
    if instr:
        for name, cfg in catalog.items():
            applies = cfg.get('instruments') or cfg.get('applies_to') or cfg.get('instrument_ids')
            if isinstance(applies, (list, tuple)) and instr in [str(x).strip() for x in applies]:
                return cfg

    requested_name = bond_data.get('discount_curve_name') or bond_data.get('curve_name')
    if requested_name:
        if requested_name not in catalog:
            raise ValueError(f'Requested discount_curve_name not found: {requested_name}')
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


# ---------------------------------------------------------------------------
# Structured-note shared utilities
# (used by both spire.py and index_linked.py)
# ---------------------------------------------------------------------------

def get_frequency(name: str):
    frequencies = {
        'Annual': ql.Annual,
        'Semiannual': ql.Semiannual,
        'Quarterly': ql.Quarterly,
        'Monthly': ql.Monthly,
    }
    if name not in frequencies:
        raise ValueError(f'Unsupported frequency: {name}')
    return frequencies[name]


def get_business_day_convention(name: str):
    conventions = {
        'Following': ql.Following,
        'ModifiedFollowing': ql.ModifiedFollowing,
        'Unadjusted': ql.Unadjusted,
    }
    if name not in conventions:
        raise ValueError(f'Unsupported business day convention: {name}')
    return conventions[name]


def build_regular_schedule(start_date, end_date, frequency_name, calendar_name, bdc_name):
    frequency  = get_frequency(frequency_name)
    calendar   = get_calendar(calendar_name)
    convention = get_business_day_convention(bdc_name)
    return ql.Schedule(
        start_date,
        end_date,
        ql.Period(frequency),
        calendar,
        convention,
        convention,
        ql.DateGeneration.Forward,
        False,
    )


def build_note_dates(note_data):
    """Return the list of ql.Date schedule dates for a structured note."""
    issue_date    = parse_date(note_data['issue_date'])
    maturity_date = parse_date(note_data['maturity_date'])

    if 'first_coupon_date' in note_data:
        dates   = [issue_date, parse_date(note_data['first_coupon_date'])]
        current = dates[-1]
        while current < maturity_date:
            next_date = ql.Date(current.dayOfMonth(), current.month(), current.year() + 1)
            if next_date > maturity_date:
                next_date = maturity_date
            dates.append(next_date)
            current = next_date
        return dates

    freq = note_data.get('coupon_frequency', 'Annual')
    if freq is None or str(freq).strip().lower() in {'none', 'null', ''}:
        return [issue_date, maturity_date]

    schedule = build_regular_schedule(
        issue_date,
        maturity_date,
        freq,
        note_data.get('calendar', 'TARGET'),
        note_data.get('business_day_convention', 'Following'),
    )
    return [schedule[i] for i in range(len(schedule))]


def discount_factor_with_issuer_spread(curve, day_count, evaluation_date, target_date, issuer_spread_bp):
    t = day_count.yearFraction(evaluation_date, target_date)
    if t < 0.0:
        return 0.0
    return curve.discount(target_date) * math.exp(-(issuer_spread_bp / 10000.0) * t)


def inflation_factor(eval_date, pay_date, inflation_assumption):
    """Project the index ratio at pay_date using a flat annual growth assumption."""
    if pay_date <= eval_date:
        return float(inflation_assumption.get('index_ratio_at_eval', 1.0))
    base_ratio   = float(inflation_assumption.get('index_ratio_at_eval', 1.0))
    annual_infl  = float(inflation_assumption.get('annual_inflation_rate', 0.02))
    yf           = ql.Actual365Fixed().yearFraction(eval_date, pay_date)
    return base_ratio * ((1.0 + annual_infl) ** yf)


def _select_from_catalog(curve_json, requested_name=None, default_currency='EUR'):
    catalog = normalize_curve_catalog(curve_json)
    if catalog is None:
        return curve_json
    if requested_name:
        if requested_name not in catalog:
            raise ValueError(f'Requested curve_name not found: {requested_name}')
        return catalog[requested_name]
    default_name = f'{default_currency.upper()}_OIS_PROXY'
    if default_name in catalog:
        return catalog[default_name]
    for name, cfg in catalog.items():
        upper = name.upper()
        if upper.startswith(f'{default_currency.upper()}_') and 'OIS' in upper and 'pillars' in cfg:
            return cfg
    raise ValueError(f'No default OIS curve found for currency={default_currency}.')


def select_note_curve(note_data, curve_json):
    """Return (curve_config, curve_name) for the note leg."""
    requested = note_data.get('discount_curve_name') or note_data.get('note_discount_curve_name')
    currency  = str(note_data.get('currency', 'EUR')).upper()
    cfg       = _select_from_catalog(curve_json, requested_name=requested, default_currency=currency)
    return cfg, cfg.get('curve_name', 'UNNAMED_CURVE')


def select_collateral_curve(note_data, curve_json):
    """Return (curve_config, curve_name) for the collateral leg."""
    collateral = note_data.get('collateral', {})
    requested  = collateral.get('discount_curve_name')
    currency   = (
        collateral.get('currency')
        or note_data.get('csa', {}).get('base_currency')
        or infer_currency_from_isin(collateral.get('isin'))
        or note_data.get('currency')
        or 'EUR'
    )
    cfg = _select_from_catalog(curve_json, requested_name=requested, default_currency=str(currency).upper())
    return cfg, cfg.get('curve_name', 'UNNAMED_CURVE')


def model_collateral_pv(collateral_data, curve, curve_day_count):
    """Price the collateral bond and return PV in absolute terms."""
    eval_date    = ql.Settings.instance().evaluationDate
    issue_date   = parse_date(collateral_data['issue_date'])
    maturity_date = parse_date(collateral_data['maturity_date'])
    principal    = float(collateral_data['principal_amount'])
    spread_bp    = float(collateral_data.get('collateral_spread_bp',
                         collateral_data.get('collateral_spread', 0.0)))

    explicit_rate = collateral_data.get('coupon_rate')
    tranches      = collateral_data.get('tranches')
    if explicit_rate is not None:
        coupon_rate = float(explicit_rate)
    elif tranches:
        total_principal = 0.0
        coupon_rate     = 0.0
        for tr in tranches:
            tp   = float(tr.get('principal', 0.0))
            ctype = tr.get('coupon_type', 'fixed')
            if ctype in ('inflation_linked', 'fixed'):
                tc = float(tr.get('coupon_rate', 0.0))
            elif ctype == 'floating':
                tc = 0.03 + float(tr.get('coupon_spread_bp', 0.0)) / 10000.0
            else:
                tc = 0.0
            coupon_rate     += tp * tc
            total_principal += tp
        coupon_rate = coupon_rate / total_principal if total_principal > 0 else 0.0
    else:
        coupon_rate = 0.0

    schedule = build_regular_schedule(
        issue_date,
        maturity_date,
        collateral_data.get('coupon_frequency', 'Semiannual'),
        collateral_data.get('calendar', 'TARGET'),
        collateral_data.get('business_day_convention', 'Following'),
    )
    day_count          = get_day_count(collateral_data.get('day_count', 'ActualActual'))
    inflation_assump   = collateral_data.get('inflation_assumption', {})

    pv_model  = 0.0
    cashflows = []
    for i in range(1, len(schedule)):
        d0 = schedule[i - 1]
        d1 = schedule[i]
        if d1 <= eval_date:
            continue
        accrual    = day_count.yearFraction(d0, d1)
        idx_ratio  = inflation_factor(eval_date, d1, inflation_assump)
        coupon_cf  = principal * coupon_rate * accrual * idx_ratio
        df         = discount_factor_with_issuer_spread(curve, curve_day_count, eval_date, d1, spread_bp)
        pv_cf      = coupon_cf * df
        pv_model  += pv_cf
        cashflows.append({'date': d1.ISO(), 'type': 'coupon', 'amount': coupon_cf, 'df': df, 'pv': pv_cf})

    if maturity_date > eval_date:
        idx_ratio_mat = inflation_factor(eval_date, maturity_date, inflation_assump)
        redemption_cf = principal * idx_ratio_mat
        df_mat        = discount_factor_with_issuer_spread(curve, curve_day_count, eval_date, maturity_date, spread_bp)
        pv_red        = redemption_cf * df_mat
        pv_model     += pv_red
        cashflows.append({'date': maturity_date.ISO(), 'type': 'redemption',
                          'amount': redemption_cf, 'df': df_mat, 'pv': pv_red})

    market_dirty = collateral_data.get('market_dirty_price')
    if market_dirty is not None:
        pv_collateral     = principal * float(market_dirty) / 100.0
        valuation_method  = 'market_dirty_price'
    else:
        pv_collateral     = pv_model
        valuation_method  = 'model_curve_plus_inflation'

    return {
        'pv_collateral':         pv_collateral,
        'pv_collateral_model':   pv_model,
        'valuation_method':      valuation_method,
        'cashflows':             cashflows,
    }


def spread_cost_from_schedule(notional, schedule_dates, eval_date, curve, curve_day_count, spread_bp):
    spread = spread_bp / 10000.0
    pv     = 0.0
    for i in range(1, len(schedule_dates)):
        d0 = schedule_dates[i - 1]
        d1 = schedule_dates[i]
        if d1 <= eval_date:
            continue
        dt  = curve_day_count.yearFraction(max(d0, eval_date), d1)
        df  = curve.discount(d1)
        pv += notional * spread * dt * df
    return pv


def compute_valuation_adjustments(note_data, curve, curve_day_count):
    """Return PV of fees, funding, CSA, and residual-basis adjustments."""
    eval_date    = ql.Settings.instance().evaluationDate
    notional     = float(note_data.get('note_notional', 100_000_000.0))
    sched_dates  = build_note_dates(note_data)

    va              = note_data.get('valuation_adjustments', {})
    fees_bp         = float(va.get('fees_bp',          0.0))
    funding_bp      = float(va.get('funding_bp',       0.0))
    csa_bp          = float(va.get('csa_bp',           0.0))
    residual_bp     = float(va.get('residual_basis_bp',0.0))

    fees     = spread_cost_from_schedule(notional, sched_dates, eval_date, curve, curve_day_count, fees_bp)
    funding  = spread_cost_from_schedule(notional, sched_dates, eval_date, curve, curve_day_count, funding_bp)
    csa      = spread_cost_from_schedule(notional, sched_dates, eval_date, curve, curve_day_count, csa_bp)
    residual = spread_cost_from_schedule(notional, sched_dates, eval_date, curve, curve_day_count, residual_bp)

    return {
        'pv_fees':             fees,
        'pv_funding':          funding,
        'pv_csa':              csa,
        'pv_residual_basis':   residual,
        'pv_total_adjustments': fees + funding + csa + residual,
    }