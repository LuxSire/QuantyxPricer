"""
Shared utility functions for pricing models.

Avoids duplicating simple helpers like
- today_date_string
- normalize_rate
- parse_date (QuantLib version)
across the models/ folder.
"""

from datetime import date

import QuantLib as ql


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