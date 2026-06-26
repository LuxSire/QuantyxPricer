#!/usr/bin/env python3
"""
Termsheet -> asset JSON converter.

Usage:
  ./scripts/termsheet_to_json.py path/to/CH1563365605.pdf --out-dir assets/

Heuristic parser: extracts text from the PDF and maps it to a structured JSON
that is compatible with the barrier_convertible / hullwhite pricing models.

Dependencies: PyPDF2 (pip install PyPDF2)
"""
import re
import json
from pathlib import Path
import argparse
from typing import Optional

try:
    from PyPDF2 import PdfReader
except ImportError:
    PdfReader = None

# ---------------------------------------------------------------------------
# Text extraction
# ---------------------------------------------------------------------------

def extract_text_from_pdf(path: Path) -> str:
    if PdfReader is None:
        raise RuntimeError('PyPDF2 not installed. Run: pip install PyPDF2')
    reader = PdfReader(str(path))
    texts = []
    for p in reader.pages:
        try:
            texts.append(p.extract_text() or '')
        except Exception:
            texts.append('')
    return "\n".join(texts)


# ---------------------------------------------------------------------------
# Basic regex helpers
# ---------------------------------------------------------------------------

ISIN_RE        = re.compile(r"\b[A-Z]{2}[A-Z0-9]{9}\d\b")
PERCENT_RE     = re.compile(r"(\d{1,2}(?:[\.,]\d+)?)\s?%\s*p\.?a\.?", re.I)
DATE_DD_MM_YY  = re.compile(r"(\d{1,2})[/\-](\d{1,2})[/\-](\d{2,4})")
DATE_WORD      = re.compile(
    r"(\d{1,2})\s+(January|February|March|April|May|June|July|August"
    r"|September|October|November|December)\s+(\d{4})", re.I)
DATE_ABBR      = re.compile(r"(\d{1,2})\s+([A-Za-z]{3})\.?\s+(\d{2,4})")
CURRENCY_RE    = re.compile(r"\b(EUR|USD|GBP|CHF|JPY|AUD|CAD)\b")

MONTHS_LONG = {
    'january':'01','february':'02','march':'03','april':'04','may':'05','june':'06',
    'july':'07','august':'08','september':'09','october':'10','november':'11','december':'12'
}
MONTHS_ABBR = {k[:3]: v for k, v in MONTHS_LONG.items()}


def _normalize_date(day: str, month: str, year: str) -> str:
    if month.isdigit():
        mo = int(month)
    else:
        mo = int(MONTHS_LONG.get(month.lower(), MONTHS_ABBR.get(month.lower()[:3], '01')))
    if len(year) == 2:
        year = '20' + year
    return f"{int(year):04d}-{mo:02d}-{int(day):02d}"


def _parse_dmy(dd: str, mm: str, yy: str) -> str:
    yy = yy if len(yy) == 4 else ('20' + yy)
    return f"{int(yy):04d}-{int(mm):02d}-{int(dd):02d}"


def find_dates(text: str):
    dates = []
    for m in DATE_DD_MM_YY.finditer(text):
        d, mo, y = m.groups()
        dates.append(_parse_dmy(d, mo, y))
    for m in DATE_WORD.finditer(text):
        d, mo, y = m.groups()
        dates.append(_normalize_date(d, mo, y))
    for m in DATE_ABBR.finditer(text):
        d, mo, y = m.groups()
        dates.append(_normalize_date(d, mo, y))
    return dates


def find_first_isin(text: str):
    m = ISIN_RE.search(text)
    return m.group(0) if m else None


def find_currency(text: str):
    m = CURRENCY_RE.search(text)
    return m.group(1) if m else None


def find_first_pa_percent(text: str):
    m = PERCENT_RE.search(text)
    if not m:
        return None
    v = m.group(1).replace(',', '.')
    try:
        return float(v) / 100.0
    except Exception:
        return None


def _find_date_after_keyword(text: str, keyword: str) -> Optional[str]:
    idx = text.lower().find(keyword.lower())
    if idx < 0:
        return None
    excerpt = text[idx: idx + 300]
    dates = find_dates(excerpt)
    return dates[0] if dates else None


def _clean_amount(raw: str) -> float:
    return float(raw.replace("'", "").replace(",", "").replace(" ", ""))


# ---------------------------------------------------------------------------
# Product-type detection
# ---------------------------------------------------------------------------

def detect_product_type(text: str):
    lower = text.lower()
    is_brc = (
        'barrier reverse convertible' in lower
        or 'multi barrier reverse convertible' in lower
        or 'sspa product type: 1230' in lower
        or 'sspa product type:1230' in lower
    )
    is_cln = 'credit-linked' in lower or 'credit linked note' in lower
    if is_brc:
        return {
            'model': 'barrier_convertible',
            'bond_type': 'barrier_reverse_convertible',
            'kind_id': 'structured_product',
            'subkind_id': '1230',
            'subkind_name_eng': 'Yield-Enhancement Products',
            'vid_name_eng': 'Barrier Reverse Convertible',
            'coupon_type_id': 'fixed_with_underlying_option',
            'structured_note': '1',
        }
    if is_cln:
        return {'model': 'cln', 'bond_type': 'credit_linked_note', 'kind_id': 'bond'}
    return {'model': 'hullwhite', 'bond_type': None, 'kind_id': 'bond'}


# ---------------------------------------------------------------------------
# Barrier-convertible specific extractors
# ---------------------------------------------------------------------------

# "USD 63.50 paid on 01/10/2026" or "CHF 11.00 paid on 25.09.2026"
COUPON_LINE_RE = re.compile(
    r'(?:USD|CHF|EUR|GBP)\s*([\d\'\,\.]+)\s+paid\s+on\s+(\d{2}[/\-\.]\d{2}[/\-\.]\d{2,4})',
    re.I
)

# Autocall table row: "1 24/12/2026 100.00% 04/01/2027"
AUTOCALL_ROW_RE = re.compile(
    r'\b(\d{1,2})\s+(\d{2}/\d{2}/\d{4})\s+\d+(?:[.,]\d+)?\s*%\s+(\d{2}/\d{2}/\d{4})'
)

# Barrier observation period: "24/06/2026 - 24/09/2027"
BARRIER_OBS_RE = re.compile(
    r'[Bb]arrier\s+[Oo]bservation\s+[Pp]eriod[\s\S]{0,30}?'
    r'(\d{2}/\d{2}/\d{4})\s*[\-–]\s*(\d{2}/\d{2}/\d{4})'
)

DENOMINATION_RE = re.compile(
    r'[Dd]enomination[\s\S]{0,10}?(?:USD|CHF|EUR|GBP)\s*([\d\'\,]+)'
)
ISSUE_SIZE_RE = re.compile(
    r'[Ii]ssue\s+[Ss]ize[\s\S]{0,10}?(?:USD|CHF|EUR|GBP)\s*([\d\'\,]+)'
)

# Barrier level pct: "(49.00%)*"  or "49.00%"
BARRIER_PCT_RE = re.compile(r'[Bb]arrier\s+[Ll]evel[^%\d]{0,30}(\d+(?:[.,]\d+)?)\s*%')

# Conversion ratio: float following the word "Conversion" or at end of underlying row
CONVERSION_RATIO_RE = re.compile(r'[Cc]onversion\s+[Rr]atio[^\d]{0,30}([\d.]+)')


def extract_coupon_schedule(text: str):
    schedule = []
    for m in COUPON_LINE_RE.finditer(text):
        raw_amt, raw_date = m.groups()
        try:
            amount = _clean_amount(raw_amt)
            parts = re.split(r'[/\-\.]', raw_date)
            if len(parts) == 3:
                d, mo, y = parts
                date_str = _parse_dmy(d, mo, y)
                schedule.append({'date': date_str, 'amount': amount})
        except Exception:
            pass
    return schedule


def extract_autocall_schedule(text: str):
    schedule = []
    for m in AUTOCALL_ROW_RE.finditer(text):
        _, obs_raw, red_raw = m.groups()
        try:
            d, mo, y = obs_raw.split('/')
            obs = _parse_dmy(d, mo, y)
            d, mo, y = red_raw.split('/')
            red = _parse_dmy(d, mo, y)
            schedule.append({'observation_date': obs, 'redemption_date': red})
        except Exception:
            pass
    return schedule


def extract_barrier_observation_period(text: str):
    m = BARRIER_OBS_RE.search(text)
    if not m:
        return None
    start_raw, end_raw = m.groups()
    try:
        d, mo, y = start_raw.split('/')
        start = _parse_dmy(d, mo, y)
        d, mo, y = end_raw.split('/')
        end = _parse_dmy(d, mo, y)
        return {'start': start, 'end': end, 'type': 'continuous'}
    except Exception:
        return None


def extract_denomination(text: str) -> Optional[float]:
    m = DENOMINATION_RE.search(text)
    if not m:
        return None
    try:
        return _clean_amount(m.group(1))
    except Exception:
        return None


def extract_issue_size(text: str) -> Optional[float]:
    m = ISSUE_SIZE_RE.search(text)
    if not m:
        return None
    try:
        return _clean_amount(m.group(1))
    except Exception:
        return None


def extract_barrier_pct(text: str) -> Optional[float]:
    m = BARRIER_PCT_RE.search(text)
    if not m:
        return None
    try:
        return float(m.group(1).replace(',', '.'))
    except Exception:
        return None


def extract_issuer(text: str) -> Optional[str]:
    m = re.search(r'[Ii]ssuer\s*\n\s*(.+?)(?:\n|$)', text)
    if m:
        return m.group(1).strip()
    m = re.search(r'[Ii]ssuer\s{1,5}([A-Z][^\n]{5,60})', text)
    if m:
        return m.group(1).strip()
    return None


def extract_underlyings(text: str):
    """
    Extract underlyings from the underlying table.
    Looks for rows with numeric levels and conversion ratios near company names.
    Returns list of underlying dicts.
    """
    # Pattern for an underlying table row:
    # Company  Exchange  Ticker  Level  Barrier  Strike  Autocall  ConvRatio
    # We'll try to find rows by looking for lines with multiple USD amounts + a decimal
    ROW_RE = re.compile(
        r'([A-Z][A-Z &\-\.0-9]{3,40}?)\s+'      # company name
        r'(NYSE|NASDAQ|SIX|LSE|XETRA|EURONEXT|Euronext)[^\n]{0,40}\n?'
        r'(?:[A-Z]{2,8}\s+(?:UN|UQ|SW|GY|FP|LN|US|CN)?)?\s*'  # ticker (optional)
        r'USD\s+([\d\.]+)\s+'                    # initial fixing
        r'USD\s+([\d\.]+)\s+'                    # barrier absolute
        r'USD\s+([\d\.]+)\s+'                    # strike absolute
        r'USD\s+([\d\.]+)\s+'                    # autocall level
        r'([\d\.]+)',                             # conversion ratio
        re.DOTALL
    )
    results = []
    for m in ROW_RE.finditer(text):
        try:
            name, exchange, initial, barrier, strike, autocall, conv = m.groups()
            initial_f = float(initial)
            barrier_f = float(barrier)
            results.append({
                'name': name.strip(),
                'exchange': exchange.strip(),
                'asset_type': 'equity',
                'currency': find_currency(text) or 'USD',
                'initial_fixing_level': initial_f,
                'barrier_level': barrier_f,
                'barrier_level_pct_of_initial': round(100.0 * barrier_f / initial_f, 2) if initial_f else None,
                'strike_level': float(strike),
                'strike_level_pct_of_initial': round(100.0 * float(strike) / initial_f, 2) if initial_f else None,
                'autocall_trigger_level': float(autocall),
                'conversion_ratio': float(conv),
                'volatility': None,
                'drift': 0,
                'dividend_yield': 0,
            })
        except Exception:
            pass
    return results


# ---------------------------------------------------------------------------
# Main heuristic function
# ---------------------------------------------------------------------------

def heuristic_field_from_text(text: str):
    isin     = find_first_isin(text)
    currency = find_currency(text)
    coupon   = find_first_pa_percent(text)
    dates    = find_dates(text)
    lname    = text.lower()

    # dates: maturity / issue
    maturity = _find_date_after_keyword(text, 'redemption date') \
            or _find_date_after_keyword(text, 'maturity date') \
            or _find_date_after_keyword(text, 'final fixing date')
    issue    = _find_date_after_keyword(text, 'issue date') \
            or _find_date_after_keyword(text, 'subscription start date')
    initial_fixing = _find_date_after_keyword(text, 'initial fixing date')

    if not issue and dates:
        issue = dates[0]
    if not maturity and dates:
        maturity = dates[-1]

    # description: first non-empty line
    first_line = next((ln.strip() for ln in text.splitlines() if ln.strip()), None)

    product = detect_product_type(text)
    is_brc  = product['model'] == 'barrier_convertible'

    result = {
        '_code': isin,
        'instrument_id': isin,
        'isin_code': isin,
        'asset_type': 'bond',
        'model': product['model'],
        'bond_type': product.get('bond_type'),
        'kind_id': product.get('kind_id'),
        'subkind_id': product.get('subkind_id'),
        'subkind_name_eng': product.get('subkind_name_eng'),
        'vid_name_eng': product.get('vid_name_eng'),
        'coupon_type_id': product.get('coupon_type_id'),
        'structured_note': product.get('structured_note', '0'),
        'currency': currency,
        'currency_id': currency,
        'currency_name': currency,
        'payment_currency_id': currency,
        'dcc_code': '30/360',
        'emission_cupon_basis_id': '30/360',
        'issue_date': issue,
        'maturity_date': maturity,
        'par': 100.0,
        'price_of_primary_placing': '100',
        'redemption_price': '100',
        'description': first_line,
        'more_eng': first_line,
        'fixed_coupon_rate': coupon,
        'curr_coupon_rate': f'{coupon * 100:.2f}' if coupon else None,
        'status_id': 'outstanding',
        'status_name_eng': 'outstanding',
        'bond_issue_form': 'uncertificated',
        'bond_issue_form_name_eng': 'Uncertificated Securities',
        'placing_type_id': 'public',
        'placing_type_name_eng': 'Public',
        'offert_eng': 'Public Offering only in Switzerland',
        'business_day_convention_id': 'following',
        'business_day_convention_name_eng': 'Following',
        'underlying_class_id': 'equity',
        'underlying_class_name_eng': 'Equity',
        'non_complex_financial_instrument': '0',
        'issuer_spread_bp': '0',
    }

    # Issuer
    issuer = extract_issuer(text)
    if issuer:
        result['emitent_name_eng'] = issuer
        result['emitent_full_name_eng'] = issuer
        result['emitent_type'] = 'bank'
        result['emitent_type_name_eng'] = 'bank'
        result['emitent_country'] = 'CH'
        result['emitent_country_name_eng'] = 'Switzerland'

    if is_brc:
        denomination = extract_denomination(text)
        issue_size   = extract_issue_size(text)
        if denomination:
            result['nominal_price']             = str(int(denomination))
            result['initial_nominal_price']     = str(int(denomination))
            result['outstanding_nominal_price'] = str(int(denomination))
            result['integral_multiple']         = str(int(denomination))
            result['minimum_investment']        = str(int(denomination))
        if issue_size:
            result['placed_volume_new']         = str(int(issue_size))
            result['initial_placement_volume']  = str(int(issue_size))

        coupon_schedule = extract_coupon_schedule(text)
        if coupon_schedule:
            result['coupon_schedule'] = coupon_schedule
            result['curr_coupon_sum'] = f"{coupon_schedule[0]['amount']:.2f}"
            result['first_coupon_end'] = coupon_schedule[0]['date']
            result['curr_coupon_date'] = coupon_schedule[-1]['date']
            result['cupon_period'] = 'quarterly' if len(coupon_schedule) >= 4 else 'semiannual'

        autocall_schedule = extract_autocall_schedule(text)
        if autocall_schedule:
            result['early_redemption_schedule'] = autocall_schedule
            result['early_redemption_date']     = autocall_schedule[0]['redemption_date']

        barrier_obs = extract_barrier_observation_period(text)
        if barrier_obs:
            result['barrier_observation_period'] = barrier_obs

        barrier_pct = extract_barrier_pct(text)

        underlyings = extract_underlyings(text)
        if underlyings:
            # For single underlying use the CH1493992296 singular field
            if len(underlyings) == 1:
                result['underlying'] = underlyings[0]
            else:
                result['underlyings'] = underlyings
                result['underlying'] = underlyings[0]  # worst-of placeholder

        result['redemption_scenarios'] = [
            {
                'scenario': 1,
                'condition': 'No barrier event',
                'payoff': 'Cash settlement equal to denomination',
            },
            {
                'scenario': 2,
                'condition': 'Barrier event and final fixing at or below strike',
                'payoff': 'Delivery of underlying (worst performer) plus cash for fractional entitlement',
            },
            {
                'scenario': 3,
                'condition': 'Barrier event and final fixing above strike',
                'payoff': 'Cash settlement equal to denomination',
            },
        ]
        result['convert_cond_eng'] = (
            'If Barrier Event has occurred and final fixing level of worst performing underlying '
            'is at or below the Strike Level, investor receives delivery of that underlying; '
            'otherwise cash settlement at denomination.'
        )

    return result


# ---------------------------------------------------------------------------
# File processor
# ---------------------------------------------------------------------------

def process_file(pdf: Path, out_dir: Path, out_file: str = None):
    print(f'Processing {pdf}...')
    try:
        text = extract_text_from_pdf(pdf)
    except Exception as e:
        print(f'  Skipped (could not extract text): {e}')
        return
    fields = heuristic_field_from_text(text)
    out_dir.mkdir(parents=True, exist_ok=True)
    if not out_file:
        name = fields.get('instrument_id') or pdf.stem
        out_file = f"{name}.json"
    out_path = out_dir / out_file
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(fields, f, indent=2, ensure_ascii=False)
    print('  Wrote', out_path)


def main():
    p = argparse.ArgumentParser(description='Convert termsheet PDF(s) to asset JSON files')
    p.add_argument('path', nargs='?', default='termsheets', help='PDF file or directory (default: termsheets)')
    p.add_argument('--out-dir', default='assets', help='Output directory for asset JSON')
    p.add_argument('--pattern', default='*.pdf', help='Filename glob when a directory is given')
    p.add_argument('--recursive', action='store_true')
    args = p.parse_args()

    src    = Path(args.path)
    out_dir = Path(args.out_dir)

    if src.is_dir():
        files = list(src.rglob(args.pattern) if args.recursive else src.glob(args.pattern))
        if not files:
            print('No PDF files found in', src)
            return
        for f in sorted(files):
            process_file(f, out_dir)
    else:
        if not src.exists():
            print('Path not found:', src)
            return
        process_file(src, out_dir)


if __name__ == '__main__':
    main()
