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


CBONDS_API_URL = "https://ws.cbonds.info/services/json/get_emissions/?lang=eng"
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
            return results[0]

        print(f"[Provider] No results found in cbonds response")
        return None
    except requests.RequestException as e:
        print(f"[Provider] cbonds API request failed for {isin_code}: {e}")
        return None
    except json.JSONDecodeError as e:
        print(f"[Provider] Failed to parse cbonds response: {e}")
        return None
    except Exception as e:
        print(f"[Provider] Unexpected error fetching from cbonds: {e}")
        return None
