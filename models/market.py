#!/usr/bin/env python3
"""
Small market data helper that calls EulerPool APIs and stores results
in `output/mkt_prices.json`.

Functions:
 - `bond_price(isin)` -> fetch bond market price by ISIN and save result
 - `sovereign_yield_curve(country)` -> fetch sovereign yield curve for a given country and save result

The module reads `EULERPOOL_API_KEY` from the environment or from a
`.env` file located at the project root.
"""
import eodhd
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

import requests


BASE = "https://api.eulerpool.com"


def _load_env(project_root: Path):
    env_path = project_root / ".env"
    if not env_path.exists():
        return
    try:
        with open(env_path, "r") as f:
            for line in f:
                raw = line.strip()
                if not raw or raw.startswith("#"):
                    continue
                if raw.startswith("export "):
                    raw = raw[len("export "):]
                if "=" not in raw:
                    continue
                k, v = raw.split("=", 1)
                k = k.strip()
                v = v.strip()
                if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
                    v = v[1:-1]
                if k and k not in os.environ:
                    os.environ[k] = v
    except Exception:
        return


def _get_api_key() -> str:
    # try environment first, then .env at project root
    key = os.getenv("EULERPOOL_API_KEY")
    if key:
        return key
    project_root = Path(__file__).parent.parent
    _load_env(project_root)
    return os.getenv("EULERPOOL_API_KEY", "")


def _save_market_entry(entry: Dict[str, Any], out_path: Path = None):
    if out_path is None:
        out_path = Path(__file__).parent.parent / "output" / "mkt_prices.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Load existing data (list) if present, else start a list
    data = []
    if out_path.exists():
        try:
            with open(out_path, "r") as f:
                data = json.load(f) or []
        except Exception:
            data = []

    # Add timestamp and append
    entry.setdefault("fetched_at_utc", datetime.utcnow().isoformat() + "Z")
    data.append(entry)

    # Atomic write
    tmp = out_path.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    tmp.replace(out_path)


def bond_price(isin: str) -> Dict[str, Any]:
    """Fetch bond price by ISIN from EulerPool and save to output file.

    Returns the JSON response from the API on success. Raises on network errors.
    """
    api_key = _get_api_key()
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    url = f"{BASE}/v1/bonds/{isin}/price"

    resp = requests.get(url, headers=headers, timeout=20)
    resp.raise_for_status()
    data = resp.json()

    entry = {"type": "bond_price", "isin": isin, "result": data}
    _save_market_entry(entry)
    return data


def sovereign_yield_curve(country: str) -> Dict[str, Any]:
    """Fetch a sovereign yield curve for `country` from EulerPool and save to output file.

    Example: `curve = sovereign_yield_curve("US")`
    """
    api_key = _get_api_key()
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    url = f"{BASE}/v1/bonds/yield-curve/{country}"

    resp = requests.get(url, headers=headers, timeout=20)
    resp.raise_for_status()
    data = resp.json()

    entry = {"type": "sovereign_yield_curve", "country": country, "result": data}
    _save_market_entry(entry)
    return data


def cbonds_bond_info(isin: str, save: bool = True) -> Dict[str, Any]:
    """Query CBonds demo JSON service using demo credentials.

    This uses the public demo endpoint with auth (test/test) and returns
    the parsed JSON. The `isin` is included in the saved entry but the
    demo endpoint itself does not accept an ISIN parameter.
    """
    url = "https://ws.cbonds.info/services/json/demo/?lang=eng"
    try:
        r = requests.get(url, auth=("test", "test"), timeout=20)
        r.raise_for_status()
        ct = r.headers.get("Content-Type", "") or ""
        text = r.text
        # If JSON content-type or text starts with JSON, parse, else return raw text
        if "application/json" in ct.lower() or text.lstrip().startswith(("{", "[")):
            try:
                data = r.json()
            except Exception:
                data = text
        else:
            data = text
    except Exception:
        # propagate network errors to caller
        raise

    if save:
        entry = {"type": "cbonds_demo", "isin": isin, "result": data}
        _save_market_entry(entry)

    return data


if __name__ == "__main__":
    # quick smoke test (won't run network if API key missing)
    try:
        apikey = _get_api_key()
        if not apikey:
            print("EULERPOOL_API_KEY not set; set it in environment or .env to enable live calls")
        else:
            print("Fetching US yield curve...")
            print(sovereign_yield_curve("US"))
    except Exception as e:
        print("Error during market fetch:", e)


def eodhd_bond_fundamentals(isin: str, save: bool = True):
    """Fetch bond fundamentals using the `eodhd` library and display as a DataFrame.

    This function expects `EODHD_API_KEY` to be set in the environment or in
    the project `.env` file. It tries to call `api.get_bonds_fundamentals_data`
    on the imported `eodhd` module. If `pandas` is available, the result is
    converted to a `DataFrame` and printed. The raw result is appended to
    `output/mkt_prices.json` when `save=True`.
    """
    project_root = Path(__file__).parent.parent
    _load_env(project_root)
    api_key = os.getenv("EODHD_API_KEY")
    if not api_key:
        print("EODHD_API_KEY not set in environment or .env")
        return None


    # Try to construct an API client from common names
    api = None
    for cls_name in ("APIClient", "ApiClient", "Api", "EodApi", "EodHdApi", "EodClient"):
        cls = getattr(eodhd, cls_name, None)
        if cls:
            try:
                api = cls(api_key)
                break
            except Exception:
                try:
                    api = cls()
                    # try setting attribute
                    setattr(api, "api_key", api_key)
                    break
                except Exception:
                    api = None

    if api is None:
        # Some packages require a factory function
        if hasattr(eodhd, "EodApi"):
            try:
                api = eodhd.EodApi(api_key)
            except Exception:
                api = None

    if api is None:
        # Can't instantiate the installed eodhd client; continue and
        # try module-level helper functions and HTTP fallbacks below.
        print("Could not instantiate eodhd API client automatically; will try module-level functions and HTTP fallbacks.")

    # Try to call get_bonds_fundamentals_data
    bonds = None
    # If API client exposes a `bonds` namespace with `get`, prefer that
    try:
        if api is not None and hasattr(api, "bonds") and hasattr(api.bonds, "get"):
            try:
                bonds = api.bonds.get(isin)
            except TypeError:
                try:
                    bonds = api.bonds.get(isin=isin)
                except Exception:
                    bonds = None
            except Exception:
                bonds = None
    except Exception:
        bonds = None
    for fn in ("get_bonds_fundamentals_data", "get_bond_fundamentals", "get_bonds_fundamentals"):
        if hasattr(api, fn):
            try:
                bonds = getattr(api, fn)(isin=isin)
                break
            except TypeError:
                # maybe different signature
                try:
                    bonds = getattr(api, fn)(isin)
                    break
                except Exception:
                    bonds = None
            except Exception:
                bonds = None

    if bonds is None:
        # Last resort: try module-level function
        if hasattr(eodhd, "get_bonds_fundamentals_data"):
            try:
                bonds = eodhd.get_bonds_fundamentals_data(isin=isin, api_key=api_key)
            except Exception:
                bonds = None

    if bonds is None:
        # If the installed eodhd client API didn't match expectations,
        # fall back to calling known EOD Historical Data REST endpoints.
        tried = []
        candidates = [
            f"https://eodhistoricaldata.com/api/bonds/{isin}.json?api_token={api_key}",
            f"https://eodhistoricaldata.com/api/bonds/{isin}?api_token={api_key}",
            f"https://eodhistoricaldata.com/api/fundamentals/bonds/{isin}.json?api_token={api_key}",
            f"https://eodhistoricaldata.com/api/fundamentals/bonds/{isin}?api_token={api_key}",
            f"https://eodhd.com/api/bonds/{isin}?api_token={api_key}",
        ]

        for url in candidates:
            try:
                tried.append(url)
                r = requests.get(url, timeout=20)
                if r.status_code == 200:
                    try:
                        bonds = r.json()
                    except Exception:
                        bonds = r.text
                    break
            except Exception:
                continue

        if bonds is None:
            print(f"Could not retrieve bond fundamentals for {isin} using eodhd client or HTTP fallbacks. Tried: {tried}")
            return None

    # Convert to DataFrame if possible
    df = None
    try:
        import pandas as pd
        df = pd.DataFrame(bonds)
        print(df)
    except Exception:
        print("Pandas not available or conversion failed; returning raw data.")

    if save:
        entry = {"type": "eodhd_bond_fundamentals", "isin": isin, "result": bonds}
        _save_market_entry(entry)

    return df if df is not None else bonds
