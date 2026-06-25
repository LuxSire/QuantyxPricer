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

def today_date_string():
    return date.today().strftime('%d-%m-%Y')



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