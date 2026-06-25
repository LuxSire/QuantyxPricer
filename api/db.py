import json
import os
from pathlib import Path
from typing import Any, Dict, Union, Optional

import mysql.connector
from dotenv import load_dotenv
from mysql.connector import MySQLConnection


PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = PROJECT_ROOT / '.env'


# Load root .env so DB_USER / DB_PASS / DB_SERVER / DB_DATABASE are available.
load_dotenv(ENV_FILE)


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if value is None or value == '':
        raise ValueError(f'Missing required environment variable: {name}')
    return value


def get_connection() -> MySQLConnection:
    user = _required_env('DB_USER')
    password = _required_env('DB_PASS')
    host = _required_env('DB_SERVER')
    database = _required_env('DB_DATABASE')
    port = int(os.getenv('DB_PORT', '3306'))

    return mysql.connector.connect(
        host=host,
        user=user,
        password=password,
        database=database,
        port=port,
        charset='utf8mb4',
        use_unicode=True,
    )


def insert_asset(payload: Union[Dict[str, Any], str]) -> int:
    """Insert one asset JSON by calling stored procedure insert_asset.

    Stored procedure contract:
      insert_asset(IN p_json JSON, OUT p_id BIGINT)

    Returns:
      Inserted row id (p_id)
    """
    if isinstance(payload, dict):
        payload_json = json.dumps(payload, ensure_ascii=False)
    elif isinstance(payload, str):
        payload_json = payload
    else:
        raise TypeError('payload must be a dict or JSON string')

    conn = get_connection()
    try:
        cursor = conn.cursor()
        try:
            # callproc returns a sequence with OUT values populated.
            result_args = cursor.callproc('insert_asset', [payload_json, 0])
            conn.commit()
        finally:
            cursor.close()

        inserted_id = int(result_args[1])
        return inserted_id
    finally:
        conn.close()


def insert_prices(payload: Union[Dict[str, Any], list, str]) -> int:
    """Insert price JSON by calling stored procedure insert_prices.

    Stored procedure contract:
      insert_prices(IN p_json JSON, OUT p_id BIGINT)

    Returns:
      Inserted row id (p_id)
    """
    if isinstance(payload, (dict, list)):
        payload_json = json.dumps(payload, ensure_ascii=False)
    elif isinstance(payload, str):
        payload_json = payload
    else:
        raise TypeError('payload must be a dict, list, or JSON string')

    conn = get_connection()
    try:
        cursor = conn.cursor()
        try:
            cursor.callproc('insert_prices', [payload_json])
            conn.commit()
            inserted_id = cursor.lastrowid or 0
        finally:
            cursor.close()

        return int(inserted_id)
    finally:
        conn.close()


def _decode_json_row(row: Dict[str, Any], json_field: str = 'json') -> Dict[str, Any]:
    raw = row.get(json_field)
    if raw is None:
        obj = {}
    elif isinstance(raw, (bytes, bytearray)):
        obj = json.loads(raw.decode('utf-8'))
    else:
        obj = json.loads(raw)
    if 'my_row_id' in row:
        obj['_id'] = row['my_row_id']
    if 'code' in row:
        obj['_code'] = row['code']
    return obj


def select_assets() -> list[Dict[str, Any]]:
    """Return all rows from the assets table as a list of parsed JSON dicts.

    Each row's `json` blob is decoded and returned as a Python dict.
    The `code` and `my_row_id` columns are merged in under the keys
    `_code` and `_id` so they are always accessible.
    """
    conn = get_connection()
    try:
        cursor = conn.cursor(dictionary=True)
        try:
            cursor.execute('SELECT my_row_id, code, json FROM assets')
            rows = cursor.fetchall()
        finally:
            cursor.close()

        return [_decode_json_row(row) for row in rows]
    finally:
        conn.close()

def select_asset(code: str) -> Optional[Dict[str, Any]]:
    """Return the first matching row from the assets table as a parsed JSON dict.

    Each row's `json` blob is decoded and returned as a Python dict.
    The `code` and `my_row_id` columns are merged in under the keys
    `_code` and `_id`. Returns `None` if no matching row exists.
    """
    conn = get_connection()
    try:
        cursor = conn.cursor(dictionary=True)
        try:
            cursor.execute('SELECT  code, json FROM assets WHERE code=%s LIMIT 1', (code,))
            row = cursor.fetchone()
        finally:
            cursor.close()

        if not row:
            return None

        return _decode_json_row(row)
    finally:
        conn.close()


def select_prices() -> list[Dict[str, Any]]:
    """Return all rows from the prices table as a list of parsed JSON dicts."""
    conn = get_connection()
    try:
        cursor = conn.cursor(dictionary=True)
        try:
            cursor.execute('SELECT  code, json FROM prices WHERE provider=%s', ('INTERNAL',))
            rows = cursor.fetchall()
        finally:
            cursor.close()

        return [_decode_json_row(row) for row in rows]
    finally:
        conn.close()

def select_timeseries() -> list[Dict[str, Any]]:
    """Return all rows from the prices table as a list of parsed JSON dicts."""
    conn = get_connection()
    try:
        cursor = conn.cursor(dictionary=True)
        try:
            cursor.execute('SELECT  code, json FROM prices WHERE provider=%s', ('eodhd',))
            rows = cursor.fetchall()
        finally:
            cursor.close()

        return [_decode_json_row(row) for row in rows]
    finally:
        conn.close()

def select_price(code: str) -> Optional[Dict[str, Any]]:
    """Return the first matching row from the prices table as a parsed JSON dict."""
    conn = get_connection()
    try:
        cursor = conn.cursor(dictionary=True)
        try:
            cursor.execute('SELECT code, json FROM prices WHERE provider=%s AND code=%s LIMIT 1', ('INTERNAL', code))
            row = cursor.fetchone()
        finally:
            cursor.close()

        if not row:
            return None

        return _decode_json_row(row)
    finally:
        conn.close()


def insert_prices_from_file(file_path: Union[str, Path]) -> int:
    """Load JSON price data from a file and call insert_prices."""
    path = Path(file_path)
    with path.open('r', encoding='utf-8') as f:
        payload = json.load(f)
    return insert_prices(payload)


if __name__ == '__main__':
    import sys

    if len(sys.argv) != 2:
        print('Usage: python api/db.py <path-to-prices-json>')
        sys.exit(1)

    file_path = sys.argv[1]
    inserted_id = insert_prices_from_file(file_path)
    print(f'Inserted prices row id: {inserted_id}')
