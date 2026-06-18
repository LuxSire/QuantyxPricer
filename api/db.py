import json
import os
from pathlib import Path
from typing import Any, Dict, Union

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


def insert_asset_json(payload: Union[Dict[str, Any], str]) -> int:
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

        result = []
        for row in rows:
            raw = row.get('json')
            if raw is None:
                asset = {}
            elif isinstance(raw, (bytes, bytearray)):
                asset = json.loads(raw.decode('utf-8'))
            else:
                asset = json.loads(raw)
            asset['_id'] = row['my_row_id']
            asset['_code'] = row['code']
            result.append(asset)
        return result
    finally:
        conn.close()
