#!/usr/bin/env python3
"""Populate the curves table from curves/swap_curves.json.

Clears existing rows and re-inserts all curves so the table is always
consistent with the JSON file. Safe to run multiple times.
"""

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / 'api'))

import db

CURVES_FILE = PROJECT_ROOT / 'curves' / 'swap_curves.json'


def seed_curves(verbose: bool = True) -> int:
    with open(CURVES_FILE, 'r', encoding='utf-8') as f:
        curves = json.load(f)

    conn = db.get_connection()
    try:
        cursor = conn.cursor()
        try:
            cursor.execute('DELETE FROM curves')
            count = 0
            for curve in curves:
                name = curve.get('curve_name', '').strip()
                if not name:
                    continue
                json_bytes = json.dumps(curve, ensure_ascii=False).encode('utf-8')
                cursor.execute(
                    'INSERT INTO curves (name, json) VALUES (%s, %s)',
                    (name, json_bytes),
                )
                count += 1
            conn.commit()
        finally:
            cursor.close()
    finally:
        conn.close()

    if verbose:
        print(f'[seed_curves] Inserted {count} curve(s) into DB from {CURVES_FILE}')
    return count


if __name__ == '__main__':
    seed_curves(verbose=True)
