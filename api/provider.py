"""Provider module for fetching instrument data from external APIs."""

import requests
import json
import os
from typing import Optional, Dict, Any
from pathlib import Path
from dotenv import load_dotenv


# Load environment variables from parent .env
PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = PROJECT_ROOT / '.env'
load_dotenv(ENV_FILE)

EODHD_API_TOKEN = os.getenv('EODHD_API_KEY')
EODHD_API_URL = 'https://eodhd.com/api/eod'


CBONDS_API_URL = "https://ws.cbonds.info/services/json/get_emissions/?lang=eng"
CBONDS_API_PRICES_URL = "https://ws.cbonds.info/services/json/get_tradings_new/?lang=eng"
CBONDS_API_ESTIMATES_URL = "https://ws.cbonds.info/services/json/get_tradings_new/?lang=eng"

CBONDS_LOGIN = (os.getenv('CBONDS_LOGIN') or '').strip()
CBONDS_PASSWORD = (os.getenv('CBONDS_PASSWORD') or '').strip()
CBONDS_AUTH = {
    "login": CBONDS_LOGIN,
    "password": CBONDS_PASSWORD
}


def fetch_from_cbonds(isin_code: str) -> Optional[Dict[str, Any]]:
    """
    Fetch instrument data from cbonds API.
    
    Args:
        isin_code: The ISIN code (instrument_id) to query
        
    Returns:
        The first matching instrument dict if found, None otherwise
    """
    if not isin_code or not isin_code.strip():
        return None
    
    payload = {
        "auth": CBONDS_AUTH,
        "filters": [
            {
                "field": "isin_code",
                "operator": "in",
                "value": isin_code.strip()
            }
        ],
        "quantity": {
            "limit": 10,
            "offset": 0
        },
        "sorting": [
            {
                "field": "id",
                "order": "asc"
            }
        ],
        "fields": []
    }
    
    try:
        headers = {
            "Content-Type": "application/json"
        }
        print(f"[Provider] Sending cbonds request for {isin_code}")
        print(f"[Provider] Auth login: {CBONDS_AUTH.get('login', 'NOT SET')}")
        print(f"[Provider] URL: {CBONDS_API_URL}")
        print(f"[Provider] Payload: {json.dumps(payload, indent=2)}")
        
        response = requests.post(CBONDS_API_URL, json=payload, headers=headers, timeout=10)
        print(f"[Provider] Response status: {response.status_code}")
        print(f"[Provider] Response text: {response.text[:500]}")
        
        response.raise_for_status()
        
        data = response.json()
        print(f"[Provider] Response JSON: {json.dumps(data, indent=2)[:500]}")
        
        # cbonds API returns data with either "items" or "data" arrays.
        results = []
        if isinstance(data, dict):
            if "items" in data and isinstance(data["items"], list):
                results = data["items"]
            elif "data" in data and isinstance(data["data"], list):
                results = data["data"]

        if isinstance(results, list) and len(results) > 0:
            print(f"[Provider] Found {len(results)} result(s) from cbonds")
            item = results[0]
            try:
                if isinstance(item, dict):
                    item['provider'] = 'cbonds'
            except Exception:
                pass
            return item

        print(f"[Provider] No results found in cbonds response")
        return None
    except requests.RequestException as e:
        print(f"[Provider] cbonds API request failed for {isin_code}: {e}")
        return None
    except json.JSONDecodeError as e:
            print(f"[Provider] Failed to parse cbonds response: {e}")
            return None



def fetch_prices_from_eodhd(code: str) -> Optional[Dict[str, Any]]:
    """
    Fetch end-of-day instrument data from EODHD.

    Args:
        code: The equity code to query

    Returns:
        The JSON payload as a dict if found, otherwise None
    """
    if not code or not code.strip():
        return None

    symbol = code.strip()
    endpoint = f"{EODHD_API_URL}/{symbol}?api_token={EODHD_API_TOKEN}&fmt=json"

    try:
        headers = {
            'Accept': 'application/json'
        }
        print(f"[Provider] Sending EODHD request for {symbol}")
        print(f"[Provider] URL: {endpoint}")

        response = requests.get(endpoint, headers=headers, timeout=10)
        print(f"[Provider] Response status: {response.status_code}")
        print(f"[Provider] Response text: {response.text[:500]}")

        response.raise_for_status()

        data = response.json()
        if isinstance(data, list):
            print(f"[Provider] Found EODHD data for {symbol} (list with {len(data)} records)")
            return {'provider': 'eodhd', 'instrument_id': symbol, 'data': data}
        elif isinstance(data, dict):
            print(f"[Provider] Found EODHD data for {symbol}")
            data['provider'] = 'eodhd'
            data['instrument_id'] = symbol
            return data
        else:
            print(f"[Provider] Unexpected EODHD response type: {type(data)}")
            return None

    except requests.RequestException as e:
        print(f"[Provider] EODHD API request failed for {symbol}: {e}")
        return None
    except json.JSONDecodeError as e:
        print(f"[Provider] Failed to parse EODHD response for {symbol}: {e}")
        return None
    except Exception as e:
        print(f"[Provider] Unexpected error fetching from EODHD: {e}")
        return None


def fetch_prices_from_cbonds(code: str) -> Optional[Dict[str, Any]]:
    """
    Fetch price / market data for a non-equity instrument from cbonds.

    Calls the cbonds tradings API endpoint directly to retrieve
    price data (fields like price, yield, trade_date, volume, etc.).
    """
    if not code or not code.strip():
        return None

    isin_code = code.strip()
    payload = {
        "auth": CBONDS_AUTH,
        "filters": [
            {
                "field": "isin_code",
                "operator": "eq",
                "value": isin_code
            }
        ],
        "quantity": {
            "limit": 10,
            "offset": 0
        },
        "sorting": [
            {
                "field": "trade_date",
                "order": "desc"
            }
        ],
        "fields": []
    }

    try:
        headers = {
            "Content-Type": "application/json"
        }
        print(f"[Provider] Sending cbonds prices request for {isin_code}")
        print(f"[Provider] Auth login: {CBONDS_AUTH.get('login', 'NOT SET')}")
        print(f"[Provider] URL: {CBONDS_API_URL}")
        print(f"[Provider] Payload: {json.dumps(payload, indent=2)}")

        response = requests.post(CBONDS_API_URL, json=payload, headers=headers, timeout=10)
        print(f"[Provider] Response status: {response.status_code}")
        print(f"[Provider] Response text: {response.text[:500]}")

        response.raise_for_status()

        data = response.json()
        print(f"[Provider] Response JSON: {json.dumps(data, indent=2)[:500]}")

        prices = data.get('items', [])
        if isinstance(prices, list) and len(prices) > 0:
            print(f"[Provider] Found {len(prices)} price record(s) from cbonds")
            item = prices[0]
            try:
                if isinstance(item, dict):
                    item['provider'] = 'cbonds'
                    item['instrument_id'] = item.get('isin_code', isin_code)
            except Exception:
                pass
            return item

        print(f"[Provider] No price data found in cbonds response for {isin_code}")
        return None
    except requests.RequestException as e:
        print(f"[Provider] cbonds prices API request failed for {isin_code}: {e}")
        return None
    except json.JSONDecodeError as e:
        print(f"[Provider] Failed to parse cbonds prices response: {e}")
        return None


def fetch_estimates_from_cbonds(isin_code: str) -> Optional[Dict[str, Any]]:
    """
    Fetch estimates data for an instrument from cbonds.

    Calls the cbonds estimation API endpoint to retrieve
    estimate data (fields like price_bid, price_ask, yield_bid, yield_ask, etc.).
    """
    if not isin_code or not isin_code.strip():
        return None

    isin_code = isin_code.strip()
    payload = {
        "auth": CBONDS_AUTH,
        "filters": [
            {
                "field": "isin_code",
                "operator": "eq",
                "value": isin_code
            }
        ],
        "quantity": {
            "limit": 10,
            "offset": 0
        },
        "sorting": [
            {
                "field": "date",
                "order": "desc"
            }
        ],
        "fields": []
    }

    try:
        headers = {
            "Content-Type": "application/json"
        }
        print(f"[Provider] Sending cbonds estimates request for {isin_code}")
        print(f"[Provider] Auth login: {CBONDS_AUTH.get('login', 'NOT SET')}")
        print(f"[Provider] URL: {CBONDS_API_ESTIMATES_URL}")
        print(f"[Provider] Payload: {json.dumps(payload, indent=2)}")

        response = requests.post(CBONDS_API_ESTIMATES_URL, json=payload, headers=headers, timeout=30)
        print(f"[Provider] Response status: {response.status_code}")
        print(f"[Provider] Response text: {response.text[:500]}")

        response.raise_for_status()

        data = response.json()
        print(f"[Provider] Response JSON: {json.dumps(data, indent=2)[:500]}")

        estimates = data.get('items', [])
        if isinstance(estimates, list) and len(estimates) > 0:
            print(f"[Provider] Found {len(estimates)} estimate record(s) from cbonds")
            item = estimates[0]
            try:
                if isinstance(item, dict):
                    item['provider'] = 'cbonds'
                    item['instrument_id'] = item.get('isin_code', isin_code)
            except Exception:
                pass
            return item

        print(f"[Provider] No estimate data found in cbonds response for {isin_code}")
        return None
    except requests.RequestException as e:
        print(f"[Provider] cbonds estimates API request failed for {isin_code}: {e}")
        return None
    except json.JSONDecodeError as e:
        print(f"[Provider] Failed to parse cbonds estimates response: {e}")
        return None
