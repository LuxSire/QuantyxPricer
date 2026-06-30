#!/usr/bin/env python3
"""
Scrape 5Y CDS last prices from investing.com/rates-bonds/world-cds and
update curves in swap_curves.json that carry an 'investing_cds_name' field.

Convention (consistent with ECB / Fed updaters):
  - pillars[0].rate  = CDS spread in basis points (e.g. 120.5 bp)
  - pillars[0].source = "investing.com world-cds '<row name>' (YYYY-MM-DD)"
  - curve.as_of       = today

To add a new country CDS curve to swap_curves.json, set:
  "investing_cds_name": "<substring of the row name on the investing.com table>"
e.g. "Italy" matches "Italy CDS 5 Years D".
"""

import json
import re
import sys
from datetime import date
from pathlib import Path

import requests
from bs4 import BeautifulSoup

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT / 'api') not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT / 'api'))
try:
    import db as _db
    _DB_AVAILABLE = True
except Exception:
    _db = None
    _DB_AVAILABLE = False

WORLD_CDS_URL = "https://uk.investing.com/rates-bonds/world-cds"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-GB,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://uk.investing.com/",
}


def fetch_html(url: str) -> str:
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.text


def parse_world_cds_last_prices(html: str) -> dict:
    """
    Parse the world-cds page table and return {row_name: spread_bp}.

    Rows are matched by the presence of 'CDS 5' in the row text.
    The first cell is the name; the second is the last price (spread in bp).
    """
    soup = BeautifulSoup(html, "html.parser")
    result = {}

    for tr in soup.select("tr"):
        cells = tr.find_all(["td", "th"])
        if len(cells) < 3:
            continue
        row_text = " ".join(tr.stripped_strings)
        if "CDS 5" not in row_text:
            continue

        # Layout: cell[0] = icon/flag (empty), cell[1] = name, cell[2] = last price
        name = cells[1].get_text(" ", strip=True)
        raw = cells[2].get_text(" ", strip=True)
        # Normalise European decimal comma and strip non-numeric chars
        raw = raw.replace(",", ".")
        raw = re.sub(r"[^\d.\-]", "", raw)
        try:
            result[name] = float(raw)
        except ValueError:
            pass

    if not result:
        raise ValueError(
            "No 'CDS 5' rows found — page structure may have changed or "
            "the request was blocked (Cloudflare / login wall)."
        )

    return result


def fetch_individual_cds_rate(curve_name: str) -> tuple:
    """
    Scrape the last rate from https://www.investing.com/rates-bonds/{curve_name}.
    Returns (date_str, rate_float) or (None, None) on failure.

    Tries several common HTML patterns used by investing.com instrument pages,
    so it degrades gracefully if the page layout changes.
    """
    url = f"https://www.investing.com/rates-bonds/{curve_name}"
    print(f"[CDS/individual]   GET {url}")
    try:
        html = fetch_html(url)
        print(f"[CDS/individual]   Response: {len(html)} chars")
    except Exception as e:
        print(f"[CDS/individual]   Error fetching {url}: {e}")
        return None, None

    soup = BeautifulSoup(html, "html.parser")
    today_str = date.today().strftime("%Y-%m-%d")

    def _parse_raw(text: str):
        cleaned = text.replace(",", ".").strip()
        cleaned = re.sub(r"[^\d.\-]", "", cleaned)
        try:
            return float(cleaned)
        except ValueError:
            return None

    # Attempt 1: newer layout — data-test attribute
    el = soup.find(attrs={"data-test": "instrument-price-last"})
    if el:
        raw_text = el.get_text(" ", strip=True)
        print(f"[CDS/individual]   Attempt 1 (data-test): found '{raw_text}'")
        v = _parse_raw(raw_text)
        if v is not None:
            print(f"[CDS/individual]   → parsed {v}")
            return today_str, v
        print(f"[CDS/individual]   → could not parse as float")
    else:
        print(f"[CDS/individual]   Attempt 1 (data-test): not found")

    # Attempt 2: classic layout — id="last_last"
    el = soup.find(id="last_last")
    if el:
        raw_text = el.get_text(" ", strip=True)
        print(f"[CDS/individual]   Attempt 2 (id=last_last): found '{raw_text}'")
        v = _parse_raw(raw_text)
        if v is not None:
            print(f"[CDS/individual]   → parsed {v}")
            return today_str, v
        print(f"[CDS/individual]   → could not parse as float")
    else:
        print(f"[CDS/individual]   Attempt 2 (id=last_last): not found")

    # Attempt 3: partial class matches common in investing.com bundles
    for sel in [
        "[class*='priceWrapper']",
        "[class*='last-price']",
        "[class*='instrument-price']",
        "[class*='header-price']",
    ]:
        matches = soup.select(sel)
        print(f"[CDS/individual]   Attempt 3 ({sel}): {len(matches)} match(es)")
        for el in matches:
            raw = el.get_text(" ", strip=True).replace(",", ".")
            nums = re.findall(r"\b\d+(?:\.\d+)?\b", raw)
            print(f"[CDS/individual]     text='{raw[:80]}' nums={nums[:5]}")
            for n in nums:
                try:
                    v = float(n)
                    if v > 0:
                        print(f"[CDS/individual]   → parsed {v}")
                        return today_str, v
                except ValueError:
                    pass

    print(f"[CDS/individual]   Could not extract price from {url} — page layout may have changed")
    return None, None


def _country_from_row_name(row_name: str) -> str:
    """'Italy CDS 5 Years' → 'Italy',  'South Korea CDS 5 Year' → 'South Korea'."""
    return re.sub(r"\s+CDS\s+5\s+Years?.*", "", row_name, flags=re.IGNORECASE).strip()


def _curve_name_from_country(country: str) -> str:
    """'South Korea' → 'CDS_SOUTH_KOREA_5Y'."""
    slug = re.sub(r"[^A-Za-z0-9]+", "_", country).upper().strip("_")
    return f"CDS_{slug}_5Y"


def update_swap_curves_cds(swap_curves_path=None, verbose=True) -> None:
    """
    Read swap_curves.json, fetch all CDS spreads from investing.com, then:
      - Update existing curves that have an 'investing_cds_name' field.
      - Auto-create a new curve entry for every scraped country that has
        no matching curve yet.
    """
    if swap_curves_path is None:
        project_root = Path(__file__).resolve().parent.parent
        swap_curves_path = project_root / "curves" / "swap_curves.json"
    else:
        swap_curves_path = Path(swap_curves_path)

    if verbose:
        print(f"\n[CDS] Reading {swap_curves_path}...")

    with open(swap_curves_path, "r") as f:
        _curves = json.load(f)

    if verbose:
        print(f"[CDS] Fetching {WORLD_CDS_URL} ...")

    try:
        html = fetch_html(WORLD_CDS_URL)
    except requests.exceptions.HTTPError as e:
        print(f"[CDS] HTTP error fetching page: {e}")
        return
    except Exception as e:
        print(f"[CDS] Failed to fetch page: {e}")
        return

    try:
        prices = parse_world_cds_last_prices(html)
    except ValueError as e:
        print(f"[CDS] Parse error: {e}")
        return

    if verbose:
        print(f"[CDS] Scraped {len(prices)} CDS row(s) from page.")

    today = date.today().strftime("%Y-%m-%d")

    # Build lookup: investing_cds_name (lower) → curve dict, for existing entries
    existing: dict = {}
    for c in _curves:
        key = (c.get("investing_cds_name") or "").strip().lower()
        if key:
            existing[key] = c

    updated_count = 0
    created_count = 0

    for row_name, spread_bp in prices.items():
        country = _country_from_row_name(row_name)
        country_lower = country.lower()
        source_str = f"investing.com world-cds '{row_name}' ({today})"

        # Exact match on investing_cds_name (case-insensitive)
        matched_curve = existing.get(country_lower)

        if matched_curve is not None:
            matched_curve["as_of"] = today
            if matched_curve.get("pillars"):
                matched_curve["pillars"][0]["rate"] = spread_bp
                matched_curve["pillars"][0]["source"] = source_str
            else:
                matched_curve.setdefault("pillars", []).insert(0, {
                    "tenor": "5Y", "rate": spread_bp, "source": source_str,
                })
            updated_count += 1
            if verbose:
                print(f"[CDS]   ✓ updated  {matched_curve['curve_name']} ← {spread_bp:.2f} bp")
            if _DB_AVAILABLE:
                try:
                    _db.upsert_curve(matched_curve["curve_name"], matched_curve)
                except Exception as dbe:
                    print(f"[CDS]   Warning: DB write failed for {matched_curve['curve_name']}: {dbe}")
        else:
            # Auto-create a new curve entry
            curve_name = _curve_name_from_country(country)
            new_curve = {
                "curve_name": curve_name,
                "curve_type": "cds",
                "as_of": today,
                "day_count": "Actual360",
                "calendar": "NullCalendar",
                "recovery_rate": 0.4,
                "investing_cds_name": country,
                "pillars": [
                    {"tenor": "5Y", "rate": spread_bp, "source": source_str}
                ],
            }
            _curves.append(new_curve)
            existing[country_lower] = new_curve
            created_count += 1
            if verbose:
                print(f"[CDS]   + created  {curve_name} ← {spread_bp:.2f} bp")
            if _DB_AVAILABLE:
                try:
                    _db.upsert_curve(curve_name, new_curve)
                except Exception as dbe:
                    print(f"[CDS]   Warning: DB write failed for {curve_name}: {dbe}")

    with open(swap_curves_path, "w") as f:
        json.dump(_curves, f, indent=2)

    if verbose:
        print(
            f"[CDS] ✓ Updated {updated_count}, created {created_count} curve(s). "
            f"Written to {swap_curves_path}"
        )

    # For CDS curves that exist in the DB but were not covered by the world-cds
    # page (e.g. corporate CDS), fetch their rate from an individual investing.com
    # page identified by the curve's 'investing_url' field.
    if _DB_AVAILABLE:
        try:
            db_curves = _db.select_curves()
            local_names = {c["curve_name"] for c in _curves}
            individual_updated = 0
            for db_curve in db_curves:
                name = db_curve.get("curve_name", "")
                if name in local_names:
                    continue
                if db_curve.get("curve_type") != "cds":
                    continue
                if verbose:
                    print(f"[CDS] Fetching individual page for {name}...")
                date_str, rate = fetch_individual_cds_rate(name)
                if date_str is None:
                    if verbose:
                        print(f"[CDS]   Skipped (no data)")
                    continue
                db_curve["as_of"] = date_str
                source_str = f"investing.com/rates-bonds/{name} ({date_str})"
                if db_curve.get("pillars"):
                    db_curve["pillars"][0]["rate"] = rate
                    db_curve["pillars"][0]["source"] = source_str
                else:
                    db_curve["pillars"] = [{"tenor": "5Y", "rate": rate, "source": source_str}]
                _db.upsert_curve(name, db_curve)
                individual_updated += 1
                if verbose:
                    print(f"[CDS]   ✓ updated  {name} ← {rate:.2f} bp")
            if verbose and individual_updated:
                print(f"[CDS] ✓ Updated {individual_updated} individual CDS curve(s) from investing.com pages.")
        except Exception as e:
            print(f"[CDS] Warning: individual CDS page update failed: {e}")


if __name__ == "__main__":
    update_swap_curves_cds(verbose=True)
