#!/usr/bin/env python3
"""
Sync all asset JSON files from assets/ folder to the API /assets endpoint.

Usage:
  python scripts/sync_assets_to_api.py [--api-base http://localhost:8000]
  
  Default API base is http://localhost:8000
"""
import json
import requests
import argparse
from pathlib import Path


def sync_assets_to_api(api_base: str = "http://localhost:8000"):
    """Loop through all JSON files in assets/ and POST each to the API."""
    project_root = Path(__file__).resolve().parent.parent
    assets_dir = project_root / 'assets'
    
    if not assets_dir.exists():
        print(f"Assets directory not found: {assets_dir}")
        return
    
    json_files = sorted(assets_dir.glob('*.json'))
    if not json_files:
        print(f"No JSON files found in {assets_dir}")
        return
    
    endpoint = f"{api_base}/assets"
    print(f"Syncing {len(json_files)} asset(s) to {endpoint}\n")
    
    success_count = 0
    error_count = 0
    
    for json_file in json_files:
        try:
            with open(json_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            # POST to /assets endpoint
            resp = requests.post(
                endpoint,
                json=data,
                headers={'Content-Type': 'application/json'},
                timeout=30
            )
            
            if resp.ok:
                result = resp.json()
                print(f"✓ {json_file.name} → {result.get('saved', 'saved')}")
                success_count += 1
            else:
                error_msg = resp.text[:200]
                print(f"✗ {json_file.name} - Status {resp.status_code}: {error_msg}")
                error_count += 1
        except requests.ConnectionError as e:
            print(f"✗ {json_file.name} - Connection error: {e}")
            error_count += 1
        except json.JSONDecodeError as e:
            print(f"✗ {json_file.name} - Invalid JSON: {e}")
            error_count += 1
        except Exception as e:
            print(f"✗ {json_file.name} - Error: {e}")
            error_count += 1
    
    print(f"\n--- Summary ---")
    print(f"Successful: {success_count}")
    print(f"Failed: {error_count}")
    print(f"Total: {len(json_files)}")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Sync asset JSON files to the API /assets endpoint'
    )
    parser.add_argument(
        '--api-base',
        default='http://localhost:8000',
        help='API base URL (default: http://localhost:8000)'
    )
    args = parser.parse_args()
    
    sync_assets_to_api(args.api_base)
