import os
import re
import sys
import requests
import logging
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
API_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(API_DIR))

from fastapi import FastAPI, HTTPException, Request, UploadFile, File
from pydantic import BaseModel
from pathlib import Path
from types import SimpleNamespace
import json
import time
from datetime import datetime
from fastapi.middleware.cors import CORSMiddleware
from dateutil import parser as date_parser
from typing import Any
import subprocess, sys
import threading
import uuid
import tempfile
import asyncio
import importlib.util
from fastapi.responses import JSONResponse, RedirectResponse, FileResponse
from classes import  Prices, Price, Asset, Assets, TS_Dict, Curves, Curve
import bcrypt
from models import helper
import db
import provider
import pricer
from pydantic import BaseModel

WEB_SOURCES: dict = {
    'borsa_italiana_mot': 'https://www.borsaitaliana.it/borsa/obbligazioni/mot/obbligazioni-in-euro/scheda/{isin}-MOTX.html?lang=it',
}


class PriceRequest(BaseModel):
    instrument_id: str
    
app = FastAPI(
    title="Quantyx Pricer API",
    description="API for uploading assets and pricing single or all instruments.",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    openapi_tags=[
        {"name": "General", "description": "General API endpoints."},
        {"name": "Assets", "description": "Upload and manage instrument JSON assets."},
        {"name": "Pricing", "description": "Run pricing workflows and read results."},
        {"name": "Jobs", "description": "Track asynchronous pricing jobs."},
        # {"name": "AI", "description": "RAG-based Q&A over termsheets and pricing data."},
    ],
)

# from ai.router import router as ai_router
# app.include_router(ai_router)

# Allow the frontend dev server (vite) and other local tools to call the API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:5174", "*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Avoid importing `pricer` at module import time because it imports heavy
# dependencies (QuantLib) that may not be available in the environment.
# Compute the project root and assets path locally so the server can start
# and still support saving asset JSONs without QuantLib installed.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
ASSETS_DIR: Path = PROJECT_ROOT / 'assets'
ASSETS_DIR.mkdir(parents=True, exist_ok=True)
FIELDS_DIR: Path = PROJECT_ROOT / 'models' / 'fields'
TERMSHEETS_DIR: Path = PROJECT_ROOT / 'termsheets'
TERMSHEETS_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR: Path = PROJECT_ROOT / 'output'
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Azure Blob Storage config (optional — upload is skipped gracefully if not configured)
AZURE_STORAGE_CONNECTION_STRING = os.getenv('AZURE_STORAGE_CONNECTION_STRING', '')
AZURE_CONTAINER_NAME = os.getenv('CONTAINER_NAME', '')


def _upload_blob(local_path: Path, blob_name: str) -> str | None:
    """Upload a local file to Azure Blob Storage. Returns the blob URL or None on failure."""
    if not AZURE_STORAGE_CONNECTION_STRING or not AZURE_CONTAINER_NAME:
        print('[Azure] Skipping blob upload — AZURE_STORAGE_CONNECTION_STRING or CONTAINER_NAME not set')
        return None
    try:
        from azure.storage.blob import BlobServiceClient
        client = BlobServiceClient.from_connection_string(AZURE_STORAGE_CONNECTION_STRING)
        blob_client = client.get_blob_client(container=AZURE_CONTAINER_NAME, blob=blob_name)
        with open(local_path, 'rb') as f:
            blob_client.upload_blob(f, overwrite=True)
        url = blob_client.url
        print(f'[Azure] Uploaded {blob_name} → {url}')
        return url
    except Exception as e:
        print(f'[Azure] Warning: blob upload failed for {blob_name}: {e}')
        return None


# Simple in-memory job registry for background tasks (non-persistent)
JOBS = {}
JOBS_LOCK = threading.Lock()

# Cached assets, prices, and curves loaded at startup
assets = Assets()
prices = Prices()
underlying_assets = Assets()
curves = Curves()
users: dict = {}  # keyed by email -> {email, firstname, lastname, password}

CURVES_PATH = PROJECT_ROOT / 'curves' / 'swap_curves.json'

def _load_curves() -> Curves:
    try:
        rows = db.select_curves()
        curves_dict = {}
        for item in rows:
            c = Curve.from_dict(item)
            if c.curve_name:
                curves_dict[c.curve_name] = c
        print(f'[API] _load_curves: loaded {len(curves_dict)} curves from DB')
        return Curves(curves_dict)
    except Exception as e:
        print(f'[API] Warning: could not load curves from DB: {e}')
        return Curves()


@app.on_event('startup')
async def initialize_data():
    global assets, prices, underlying_assets, curves
    try:
        loaded_assets = await fetch_assets()
        print(f"[API] Sample asset from DB: {loaded_assets[0] if loaded_assets else 'empty'}")  # ← add this
        if isinstance(loaded_assets, list) or isinstance(loaded_assets, dict):
            assets = Assets.from_data(loaded_assets)
        else:
            assets = Assets()
        print(f"[API] Loaded {len(assets)} assets from database")
    except Exception as e:
        print(f"[API] Warning: could not load assets at startup: {e}")
        assets = Assets()

    try:
        loaded_prices = await fetch_prices()
        if isinstance(loaded_prices, list) or isinstance(loaded_prices, dict):
            prices = Prices.from_data(loaded_prices)
        else:
            prices = Prices()
        print(f"[API] Loaded {len(prices)} prices from database")
    except Exception as e:
        print(f"[API] Warning: could not load prices at startup: {e}")
        prices = Prices()

    try:
        loaded_timeseries = await fetch_timeseries()
        timeseries = TS_Dict.from_data(loaded_timeseries)
        
        print(f"[API] Loaded {len(timeseries)} time series from database")
    except Exception as e:
        print(f"[API] Warning: could not load time series at startup: {e}")
        timeseries = TS_Dict()

    try:
        for asset in assets.values():
            asset.prices = prices.get(asset.instrument_id)

            def _register_underlying(ul, parent_id):
                key = ul.instrument_id
                if not key:
                    return
                ts = timeseries.get(key)
                ul.ts = ts
                underlying_assets[key] = ul
                _vol = ts.volatility() if ts is not None else 'N/A'
                print(f"[API] initialize_data: underlying_asset={key} parent={parent_id} prices_set={ts is not None} volatility={_vol}")

            if hasattr(asset, 'underlying') and asset.underlying is not None:
                key = asset.underlying.instrument_id
                asset.underlying_ts = timeseries.get(key)
                print(f"[API] initialize_data: asset={asset.instrument_id} underlying_prices_in_db={timeseries.get(key) is not None}")
                _register_underlying(asset.underlying, asset.instrument_id)

            ul_list = getattr(asset, 'underlyings', None) or []
            for ul in ul_list:
                _register_underlying(ul, asset.instrument_id)

            print(f"[API] Extracted {len(underlying_assets)} underlying assets")
    except Exception as e:
        print(f"[API] Warning: could not extract underlying assets: {e}")
        underlying_assets = Assets()

    # Load curves
    curves = _load_curves()
    print(f'[API] Loaded {len(curves)} curves from {CURVES_PATH}')

    # Load users
    global users
    try:
        loaded_users = db.select_users()
        users = {u['email']: u for u in loaded_users if u.get('email')}
        print(f'[API] Loaded {len(users)} users from database')
    except Exception as e:
        print(f'[API] Warning: could not load users at startup: {e}')
        users = {}

    # Wire AI RAG engine so it always reads the current global state, then build index in background
    # try:
    #     from ai import rag as _ai_rag
    #     import asyncio, concurrent.futures
    #     def _get_assets(): return assets
    #     def _get_prices(): return prices
    #     _ai_rag.configure(_get_assets, _get_prices)
    #     print('[API] AI RAG engine configured')
    #     loop = asyncio.get_event_loop()
    #     def _build():
    #         try:
    #             n = _ai_rag.build_index()
    #             print(f'[API] AI RAG index ready: {n} chunks')
    #         except Exception as be:
    #             print(f'[API] Warning: could not build AI RAG index at startup: {be}')
    #     loop.run_in_executor(None, _build)
    # except Exception as e:
    #     print(f'[API] Warning: could not configure AI RAG engine: {e}')


def _write_log(log_path, obj):
    try:
        with open(log_path, 'a') as lf:
            lf.write(json.dumps(obj) + '\n')
    except Exception:
        pass


def _run_price_all(job_id: str, cmd: list, log_path: Path):
    print(f'[price_all] job={job_id} starting: {" ".join(cmd)}')
    with JOBS_LOCK:
        JOBS[job_id]['status'] = 'running'
        JOBS[job_id]['start_ts'] = datetime.utcnow().isoformat() + 'Z'
    _write_log(log_path, { 'ts': datetime.utcnow().isoformat() + 'Z', 'event': 'price_all', 'job': job_id, 'phase': 'started', 'cmd': ' '.join(cmd) })
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
        stdout = proc.stdout or ''
        stderr = proc.stderr or ''
        print(f'[price_all] job={job_id} returncode={proc.returncode}')
        if stdout:
            print(f'[price_all] stdout (last 2000):\n{stdout[-2000:]}')
        if stderr:
            print(f'[price_all] stderr (last 2000):\n{stderr[-2000:]}')
        with JOBS_LOCK:
            JOBS[job_id]['stdout'] = stdout[:500000]
            JOBS[job_id]['stderr'] = stderr[:500000]
            JOBS[job_id]['returncode'] = proc.returncode
    except Exception as e:
        print(f'[price_all] job={job_id} subprocess exception: {e}')
        with JOBS_LOCK:
            JOBS[job_id]['status'] = 'failed'
            JOBS[job_id]['end_ts'] = datetime.utcnow().isoformat() + 'Z'
            JOBS[job_id]['error'] = str(e)
        _write_log(log_path, { 'ts': datetime.utcnow().isoformat() + 'Z', 'event': 'price_all', 'job': job_id, 'phase': 'failed', 'error': str(e) })
        return

    # after running, try to read output/prices.json and reload global prices
    out_path = PROJECT_ROOT / 'output' / 'prices.json'
    print(f'[price_all] job={job_id} looking for {out_path} (exists={out_path.exists()})')
    if out_path.exists():
        try:
            with open(out_path, 'r') as f:
                data = json.load(f)
            # Reload global prices from the freshly written file
            global prices
            new_prices = Prices()
            for item in (data if isinstance(data, list) else [data]):
                try:
                    p = Price.from_dict(item)
                    new_prices[p.instrument_id] = p
                except Exception as pe:
                    print(f'[price_all] skipping price entry: {pe}')
            prices = new_prices
            print(f'[price_all] job={job_id} reloaded {len(prices)} prices into memory')
            with JOBS_LOCK:
                JOBS[job_id]['status'] = 'succeeded'
                JOBS[job_id]['end_ts'] = datetime.utcnow().isoformat() + 'Z'
                JOBS[job_id]['result_count'] = len(prices)
            _write_log(log_path, { 'ts': datetime.utcnow().isoformat() + 'Z', 'event': 'price_all', 'job': job_id, 'phase': 'succeeded', 'result_count': len(prices), 'stdout': stdout[:2000] })
        except Exception as e:
            print(f'[price_all] job={job_id} failed to reload prices.json: {e}')
            with JOBS_LOCK:
                JOBS[job_id]['status'] = 'failed'
                JOBS[job_id]['end_ts'] = datetime.utcnow().isoformat() + 'Z'
                JOBS[job_id]['error'] = f'Could not read prices.json: {e}'
            _write_log(log_path, { 'ts': datetime.utcnow().isoformat() + 'Z', 'event': 'price_all', 'job': job_id, 'phase': 'read_failed', 'error': str(e) })
    else:
        print(f'[price_all] job={job_id} FAILED — prices.json not produced. returncode={proc.returncode}')
        with JOBS_LOCK:
            JOBS[job_id]['status'] = 'failed'
            JOBS[job_id]['end_ts'] = datetime.utcnow().isoformat() + 'Z'
            JOBS[job_id]['error'] = 'prices.json not produced'
        _write_log(log_path, { 'ts': datetime.utcnow().isoformat() + 'Z', 'event': 'price_all', 'job': job_id, 'phase': 'no_output', 'stdout': stdout[:2000] if 'stdout' in locals() else '' })


@app.get('/', include_in_schema=False)
async def root():
    return RedirectResponse(url='/docs')



_KEYWORD_RULES = [
    # Order matters: more-specific patterns must come before generic ones.
    (r'spire',                                                'spire'),
    (r'credit.?linked.?note|contract.?linked.?note|\bcln\b', 'cln'),
    (r'barrier.?convertible',                                 'barrier_convertible'),
    (r'inflation.?linked',                                    'inflation_linked'),
    (r'index.?linked',                                        'index_linked'),
    (r'pay.?in.?kind|\bpik\b',                               'pik'),
    (r'\brepo\b|discount.?note|commercial.?paper|t-?bill',   'discount_note'),
    (r'\bco.?co\b|\bat1\b|additional.?tier.?1|contingent.?convertible', 'at1'),
    (r'asset.?backed|\babs\b|mortgage.?backed|\bmbs\b',      'abs'),
    (r'\bclo\b|collaterali[sz]ed.?loan.?obligation',         'clo'),
    (r'autocall|reverse.?convertible',                        'autocallable_reverse_convertible'),
    (r'barrier.?discount|discount.?certificate',              'barrier_discount'),
    (r'\bconvertible\b',                                      'simple_convertible'),
    (r'trinomial',                                            'trinomialtree'),
    (r'monte.?carlo',                                         'montecarlo'),
    (r'\birs\b|interest.?rate.?swap',                        'irs'),
    (r'\bcds\b|credit.?default.?swap',                       'cds'),
    (r'\bcap\b|interest.?rate.?cap',                         'cap'),
    (r'\bfloor\b|interest.?rate.?floor',                     'floor'),
]
_KEYWORD_RE = [(re.compile(pat, re.I), model) for pat, model in _KEYWORD_RULES]

_TEXT_FIELDS = ('description', 'asset_type', 'bond_structure', 'instrument_type',
                'typology', 'coupon_structure', 'seniority', 'name')


def _infer_model(payload: dict) -> str:
    """Score each model schema against the payload and return the best-fit model name.

    First pass: keyword scan over text fields — returns immediately on a match.
    Second pass: required + optional field-coverage scoring with tiebreakers.
    """
    # --- keyword scan ---
    text = ' '.join(str(payload.get(f, '')) for f in _TEXT_FIELDS).lower()
    for regex, model in _KEYWORD_RE:
        if regex.search(text):
            return model

    _UNIVERSAL = {'instrument_id', 'evaluation_date', 'description', 'issue_date',
                  'maturity_date', 'calendar', 'currency', 'isin'}

    payload_keys = set(payload.keys())
    best_model = 'hullwhite'
    best_score = -1.0

    for schema_path in sorted(FIELDS_DIR.glob('*.json')):
        model_name = schema_path.stem
        try:
            schema = json.loads(schema_path.read_text())
        except Exception:
            continue

        required = [f['name'] for f in schema.get('required_fields', []) if f['name'] not in _UNIVERSAL]
        optional = [f['name'] for f in schema.get('optional_fields', [])]

        req_score = sum(1 for f in required if f in payload_keys) / max(len(required), 1)
        opt_score = sum(1 for f in optional if f in payload_keys) / max(len(optional), 1)
        score = req_score + 0.3 * opt_score

        # Value bonus: coupon_structure == 'index_linked' is the definitive marker
        if model_name == 'index_linked' and payload.get('coupon_structure') == 'index_linked':
            score += 0.5

        if score > best_score:
            best_score = score
            best_model = model_name

    # Tiebreak: hullwhite / trinomialtree / montecarlo share identical required fields
    if best_model in ('hullwhite', 'trinomialtree', 'montecarlo'):
        if 'tree_time_steps' in payload_keys:
            return 'trinomialtree'
        if any(k in payload_keys for k in ('mc_time_steps', 'mc_num_paths', 'mc_seed')):
            return 'montecarlo'
        return 'hullwhite'

    return best_model


async def _save_asset_data(payload: dict) -> dict:
    """Normalize dates, write asset JSON to ASSETS_DIR, and insert into MySQL."""
    asset = payload

    if not asset.get('model'):
        asset['model'] = _infer_model(asset)
        print(f"[API] model not set — inferred: {asset['model']}")

    def try_parse_date(val: Any):
        if not isinstance(val, str):
            return None
        s = val.strip()
        if not s:
            return None
        if len(s) >= 10 and s[4] == '-' and s[7] == '-':
            return s[:10]
        try:
            dt = date_parser.parse(s, dayfirst=True)
            return dt.date().isoformat()
        except Exception:
            return None

    date_keys = [
        'evaluation_date', 'maturity_date', 'first_coupon_date', 'issue_date',
        'interest_commencement_date', 'expiry_date', 'first_day_of_trading'
    ]
    for k in date_keys:
        if k in asset:
            parsed = try_parse_date(asset[k])
            if parsed:
                asset[k] = parsed
            else:
                print(f"[API] Could not parse date field {k}: {asset.get(k)}")

    instrument_id = asset.get('instrument_id') or asset.get('isin')
    if not instrument_id:
        raise HTTPException(status_code=400, detail='asset JSON must include instrument_id or isin')
    filename = f"{instrument_id}.json"
    path = ASSETS_DIR / filename
    try:
        with open(path, 'w') as f:
            json.dump(asset, f, indent=2)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    print(f"[API] Saved asset to {path} (size={path.stat().st_size} bytes)")

    try:
        row_id = db.insert_asset(asset)
        print(f"[API] Inserted asset into MySQL with id={row_id}")
    except Exception as e:
        print(f"[API] Warning: could not insert asset into MySQL: {e}")

    try:
        assets[instrument_id] = Asset.from_dict(asset) if hasattr(Asset, 'from_dict') else Asset(**asset)
    except Exception as upd_err:
        logging.warning(f'[API] Could not update in-memory assets after save: {upd_err}')

    return {"saved": filename, "path": str(path)}


@app.post('/assets', tags=['Assets'], summary='Upload an asset JSON file')
async def save_asset(request: Request, payload: dict = None):
    """Save a bond JSON into the assets/ folder.

    Body must be the bond JSON itself (object) and include `instrument_id` or `isin`.
    Returns the saved filename.
    """
    try:
        raw_body = await request.body()
        print('\n[API] /assets received request headers:')
        for k, v in request.headers.items():
            print(f'  {k}: {v}')
        print(f'[API] Raw body length: {len(raw_body)}')
    except Exception as e:
        print(f'[API] Could not read raw request body: {e}')
        raw_body = None

    if payload is None:
        try:
            payload = json.loads(raw_body.decode('utf-8')) if raw_body else None
        except Exception as e:
            raise HTTPException(status_code=400, detail=f'Invalid JSON body: {e}')

    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail='JSON object required in request body')

    return await _save_asset_data(payload)


@app.post('/update_asset', tags=['Assets'], summary='Replace an existing asset JSON by uploaded filename')
async def update_asset(file: UploadFile = File(...)):
    if not file.filename:
        raise HTTPException(status_code=400, detail='Uploaded file must include a filename')

    filename = Path(file.filename).name
    if not filename.lower().endswith('.json'):
        raise HTTPException(status_code=400, detail='Only .json files are supported')

    path = ASSETS_DIR / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail=f'Asset file not found: {filename}')

    print(f"[API] /update_asset received file: filename={filename}, content_type={file.content_type}, size={file.size if hasattr(file, 'size') else 'unknown'}")
    
    try:
        raw = await file.read()
        print(f"[API] /update_asset read {len(raw)} bytes from file")
        payload = json.loads(raw.decode('utf-8'))
        if isinstance(payload, dict):
            print(f"[API] /update_asset parsed JSON content: {json.dumps(payload, indent=2, default=str)}")
        else:
            print(f"[API] /update_asset parsed JSON type: {type(payload)}, value: {payload}")
    except Exception as e:
        print(f"[API] /update_asset error reading/parsing file: {e}")
        raise HTTPException(status_code=400, detail=f'Invalid JSON file: {e}')

    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail='JSON root must be an object')

    # Keep date normalization consistent with /assets endpoint behavior.
    def try_parse_date(val: Any):
        if not isinstance(val, str):
            return None
        s = val.strip()
        if not s:
            return None
        if len(s) >= 10 and s[4] == '-' and s[7] == '-':
            return s[:10]
        try:
            dt = date_parser.parse(s, dayfirst=True)
            return dt.date().isoformat()
        except Exception:
            return None

    date_keys = [
        'evaluation_date', 'maturity_date', 'first_coupon_date', 'issue_date',
        'interest_commencement_date', 'expiry_date', 'first_day_of_trading'
    ]
    for k in date_keys:
        if k in payload:
            parsed = try_parse_date(payload[k])
            if parsed:
                payload[k] = parsed

    try:
        with open(path, 'w') as f:
            json.dump(payload, f, indent=2)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Could not update asset file: {e}')
    
    # Also update in MySQL database
    try:
        row_id = db.insert_asset(payload)
        print(f"[API] Updated asset in MySQL with id={row_id}")
    except Exception as e:
        print(f"[API] Warning: could not update asset in MySQL: {e}")
        # Do not fail the update if DB update fails; file is already saved

    # Update the global assets cache
    try:
        instrument_id = payload.get('instrument_id') or Path(filename).stem
        if instrument_id:
            # Create Asset object from payload if possible, otherwise store dict
            try:
                asset_obj = Asset.from_dict(payload) if hasattr(Asset, 'from_dict') else Asset(**payload)
                assets[instrument_id] = asset_obj
            except Exception:
                # Fallback: store as dict
                assets[instrument_id] = payload
            print(f"[API] Updated global assets cache for instrument_id={instrument_id}")
    except Exception as e:
        print(f"[API] Warning: could not update global assets cache: {e}")

    return {"updated": filename, "path": str(path)}


@app.post('/termsheet_asset', tags=['Assets'], summary='Upload a PDF termsheet and convert it to an asset JSON')
async def termsheet_asset(file: UploadFile = File(...)):
    if not file.filename:
        raise HTTPException(status_code=400, detail='Uploaded file must include a filename')

    filename = Path(file.filename).name
    if not filename.lower().endswith('.pdf'):
        raise HTTPException(status_code=400, detail='Only .pdf files are supported for termsheet upload')

    temp_pdf = TERMSHEETS_DIR / f"{uuid.uuid4().hex}_{filename}"
    try:
        raw = await file.read()
        with open(temp_pdf, 'wb') as f:
            f.write(raw)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Could not store uploaded termsheet: {e}')

    # Upload to Azure Blob Storage (non-blocking best-effort; does not block the response)
    blob_url = _upload_blob(temp_pdf, filename)

    try:
        if str(PROJECT_ROOT) not in sys.path:
            sys.path.insert(0, str(PROJECT_ROOT))
        from scripts import termsheet_to_json as ts2j
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Could not load termsheet converter: {e}')

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        try:
            ts2j.process_file(temp_pdf, tmp_path)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f'Termsheet conversion failed: {e}')

        json_files = list(tmp_path.glob('*.json'))
        if not json_files:
            raise HTTPException(status_code=500, detail='Conversion finished but output JSON was not found')

        try:
            with open(json_files[0], 'r', encoding='utf-8') as f:
                payload = json.load(f)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f'Could not read generated JSON: {e}')

    result = await _save_asset_data(payload)
    return {
        **result,
        'instrument_id': payload.get('instrument_id') or payload.get('isin'),
        'asset': payload,
        'blob_url': blob_url,
    }


@app.get('/fetch_asset', tags=['Assets'], summary='Fetch one asset JSON by instrument_id')
async def fetch_asset(instrument_id: str):
    if not instrument_id or not instrument_id.strip():
        raise HTTPException(status_code=400, detail='instrument_id is required')

    # Keep only the basename to prevent path traversal
    safe_id = Path(instrument_id.strip()).name
    
    try:
        # Try to fetch from local database first
        asset = db.select_asset(safe_id)
        if asset is not None:
            return asset
    except Exception as e:
        print(f"[API] Error fetching from database for {safe_id}: {e}")
    
    # Fallback: try cbonds API provider
    try:
        print(f"[API] Trying cbonds provider for {safe_id}")
        cbonds_data = provider.fetch_from_cbonds(safe_id)
        if cbonds_data is not None:
            # Insert the fetched asset into the database
            try:
                row_id = db.insert_asset_json(cbonds_data)
                print(f"[API] Inserted cbonds asset into database with id={row_id}")
            except Exception as db_err:
                print(f"[API] Warning: could not insert cbonds asset into database: {db_err}")
                # Do not fail the lookup if DB insert fails; still return the data
            return cbonds_data
    except Exception as e:
        print(f"[API] Error fetching from cbonds provider for {safe_id}: {e}")
    
    # If both local and provider failed, return 404
    raise HTTPException(status_code=404, detail=f'Asset not found for instrument_id: {safe_id}')


@app.get('/fetch_assets', tags=['Assets'], summary='Fetch all assets from MySQL')
async def fetch_assets():
    try:
        assets = db.select_assets()
        return assets
    except Exception as e:
        print(f"[API] Error fetching assets from database: {e}")
        raise HTTPException(status_code=500, detail='Could not fetch assets from database')


@app.get('/fetch_models', tags=['Assets'], summary='Fetch all model names from MySQL')
async def fetch_models():
    try:
        models = db.select_models()
        return models
    except Exception as e:
        print(f'[API] Error fetching models from database: {e}')
        raise HTTPException(status_code=500, detail='Could not fetch models from database')


class UpdateModelRequest(BaseModel):
    name: str
    required_fields: list
    optional_fields: list

@app.post('/update_model', tags=['Assets'], summary='Update required and optional fields for a model')
async def update_model(payload: UpdateModelRequest):
    try:
        db.update_model(payload.name, payload.required_fields, payload.optional_fields)
        return {'ok': True, 'name': payload.name}
    except Exception as e:
        print(f'[API] Error updating model {payload.name}: {e}')
        raise HTTPException(status_code=500, detail=str(e))


def _is_equity_asset(asset: dict) -> bool:
    """Heuristic detection whether an asset represents an equity.

    Checks a few common fields used in the repository and falls back
    to checking underlying_class_name_eng/underlying_class_id.
    """
    if not isinstance(asset, dict):
        return False
    at = (asset.get('asset_type'))
    if isinstance(at, str) and at.lower() == 'equity':
        return True
    return False


@app.get('/download_prices', tags=['Pricing'], summary='Download market/pricing data for one instrument')
async def download_prices(instrument_id: str):
    if not instrument_id or not instrument_id.strip():
        raise HTTPException(status_code=400, detail='instrument_id is required')
    safe_id = Path(instrument_id.strip()).name

    # find matching asset in cached assets
    _asset = assets.get(safe_id)
    
    # if not found, try underlying_assets cache
    if _asset is None:
        underlying = underlying_assets.get(safe_id)
        if underlying is not None:
            _asset = underlying.to_dict() if hasattr(underlying, 'to_dict') else underlying
    # if not found, try DB lookup
    if _asset is None:
        try:
            _asset = db.select_asset(safe_id)
        except Exception:
            _asset = None

    # Convert Asset object to dict if needed
    asset_dict = _asset.to_dict() if hasattr(_asset, 'to_dict') else _asset if _asset else None
    
    try:
        if _asset is not None and _is_equity_asset(asset_dict):
            # for equities use EODHD
            code = asset_dict.get('ticker') or asset_dict.get('isin_code') or safe_id
            res = provider.fetch_prices_from_eodhd(code)
        elif _asset is not None:
            code = asset_dict.get('isin_code') or safe_id
            res = provider.fetch_from_cbonds(code)
        else:
            # Asset not found in cache/DB - try EODHD directly (e.g., for tickers like AAPL)
            print(f"[API] Asset {safe_id} not found in cache/DB, trying EODHD directly")
            res = provider.fetch_prices_from_eodhd(safe_id)
            if res is None:
                # Also try cbonds
                res = provider.fetch_from_cbonds(safe_id)
        
        if res is None:
            raise HTTPException(status_code=404, detail=f'No market data found for {safe_id}')
        # Stamp instrument_id so downstream insert_prices always has it
        if isinstance(res, dict) and not res.get('instrument_id'):
            res['instrument_id'] = safe_id
        elif isinstance(res, list):
            for item in res:
                if isinstance(item, dict) and not item.get('instrument_id'):
                    item['instrument_id'] = safe_id
        print(f"[API] Downloaded market data for {safe_id}: prices={res is not None}")
        return res
    except HTTPException:
        raise
    except Exception as e:
        print(f"[API] Error downloading price for {safe_id}: {e}")
        raise HTTPException(status_code=500, detail='Error fetching market data')


@app.get('/download_all_prices', tags=['Pricing'], summary='Download market/pricing data for all cached assets')
async def download_all_prices():
    results = []
    if not isinstance(assets, list):
        raise HTTPException(status_code=500, detail='Assets cache is not a list')

    for a in assets:
        try:
            if not isinstance(a, dict):
                continue
            iid = a.get('instrument_id') or a.get('isin_code') or None
            if not iid:
                continue
            if _is_equity_asset(a):
                code = a.get('bbgid_ticker') or a.get('isin_code') or iid
                res = provider.fetch_prices_from_eodhd(code)
            else:
                code = a.get('isin_code') or iid
                res = provider.fetch_from_cbonds(code)
            results.append({'instrument_id': iid, 'data': res})
        except Exception as e:
            results.append({'instrument_id': a.get('instrument_id') or a.get('isin_code'), 'error': str(e)})

    return results


@app.get('/fetch_prices', tags=['Pricing'], summary='Fetch all prices from MySQL')
async def fetch_prices():
    try:
        prices = db.select_prices()
        return prices
    except Exception as e:
        print(f"[API] Error fetching prices from database: {e}")
        raise HTTPException(status_code=500, detail='Could not fetch prices from database')


@app.get('/fetch_prices_cbonds', tags=['Pricing'], summary='Fetch cbonds prices from MySQL')
async def fetch_prices_cbonds():
    try:
        return db.select_prices_cbonds()
    except Exception as e:
        print(f"[API] Error fetching cbonds prices from database: {e}")
        raise HTTPException(status_code=500, detail='Could not fetch cbonds prices from database')


@app.get('/fetch_curves', tags=['Pricing'], summary='Return swap curves from DB')
async def fetch_curves_endpoint():
    try:
        return db.select_curves()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Could not fetch curves from DB: {e}')


@app.post('/insert_curve', tags=['Pricing'], summary='Insert a new curve into DB')
async def insert_curve(request: Request, payload: dict = None):
    if payload is None:
        try:
            payload = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail='Invalid JSON body')
    curve_name = (payload.get('curve_name') or '').strip()
    if not curve_name:
        raise HTTPException(status_code=400, detail='curve_name is required')
    try:
        row_id = db.insert_curve(curve_name, payload)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Could not insert curve: {e}')
    global curves
    curves = _load_curves()
    return {'status': 'inserted', 'curve_name': curve_name, 'row_id': row_id}


@app.post('/update_curve', tags=['Pricing'], summary='Update (upsert) a curve in DB')
async def update_curve_endpoint(request: Request, payload: dict = None):
    if payload is None:
        try:
            payload = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail='Invalid JSON body')
    curve_name = (payload.get('curve_name') or '').strip()
    if not curve_name:
        raise HTTPException(status_code=400, detail='curve_name is required')
    try:
        db.upsert_curve(curve_name, payload)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Could not update curve: {e}')
    global curves
    curves = _load_curves()
    return {'status': 'updated', 'curve_name': curve_name}

_fetch_individual_cds_rate_fn = None

def _get_fetch_individual_cds_rate():
    global _fetch_individual_cds_rate_fn
    if _fetch_individual_cds_rate_fn is None:
        spec = importlib.util.spec_from_file_location(
            'update_cds', PROJECT_ROOT / 'scripts' / 'update_cds.py'
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _fetch_individual_cds_rate_fn = mod.fetch_individual_cds_rate
    return _fetch_individual_cds_rate_fn


@app.post('/fetch_individual_cds_rate', tags=['Pricing'], summary='Fetch CDS rate from individual investing.com page and update DB')
async def fetch_individual_cds_rate_endpoint(request: Request, payload: dict = None):
    if payload is None:
        try:
            payload = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail='Invalid JSON body')
    curve_name = (payload.get('curve_name') or '').strip()
    if not curve_name:
        raise HTTPException(status_code=400, detail='curve_name is required')

    fetch_fn = _get_fetch_individual_cds_rate()
    date_str, rate = await asyncio.to_thread(fetch_fn, curve_name)

    if date_str is None:
        raise HTTPException(status_code=404, detail=f'Could not fetch rate for {curve_name} from investing.com')

    try:
        all_curves = db.select_curves()
        curve_data = next((c for c in all_curves if c.get('curve_name') == curve_name), None)
        if curve_data is None:
            curve_data = {'curve_name': curve_name, 'curve_type': 'cds'}
        curve_data['as_of'] = date_str
        source_str = f"investing.com/rates-bonds/{curve_name} ({date_str})"
        if curve_data.get('pillars'):
            curve_data['pillars'][0]['rate'] = rate
            curve_data['pillars'][0]['source'] = source_str
        else:
            curve_data['pillars'] = [{'tenor': '5Y', 'rate': rate, 'source': source_str}]
        db.upsert_curve(curve_name, curve_data)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Could not update curve in DB: {e}')

    global curves
    curves = _load_curves()
    return {'curve_name': curve_name, 'date': date_str, 'rate': rate}


@app.get('/fetch_asset_timeseries', tags=['Pricing'], summary='Fetch time series for an underlying asset by instrument_id')
async def fetch_asset_timeseries(instrument_id: str):
    if not instrument_id or not instrument_id.strip():
        raise HTTPException(status_code=400, detail='instrument_id is required')
  
    asset = underlying_assets[instrument_id]
    if asset is None:
        raise HTTPException(status_code=404, detail=f'Underlying asset not found: {instrument_id}')
    ts = asset.ts
    if ts is None:
        raise HTTPException(status_code=404, detail=f'No time series available for {instrument_id}')
    return ts.to_list()


@app.get('/fetch_timeseries', tags=['Pricing'], summary='Fetch all time series from MySQL')
async def fetch_timeseries():
    try:
        timeseries = db.select_timeseries()
        return timeseries
    except Exception as e:
        print(f"[API] Error fetching time series from database: {e}")
        raise HTTPException(status_code=500, detail='Could not fetch time series from database')

@app.post('/insert_prices', tags=['Pricing'], summary='Insert prices JSON into MySQL')
async def insert_prices(request: Request, payload: Any = None):
    """Insert price JSON into the database by calling `db.insert_prices`.

    Expects a JSON body (object or list) and returns the inserted row id.
    """
    # Read raw body if payload not provided
    raw_body = None
    try:
        raw_body = await request.body()
    except Exception:
        raw_body = None

    if payload is None:
        try:
            payload = json.loads(raw_body.decode('utf-8')) if raw_body else None
        except Exception:
            raise HTTPException(status_code=400, detail='Invalid JSON body')

    if payload is None:
        raise HTTPException(status_code=400, detail='JSON body required')

    try:
        logging.info('Inserting prices into database...')
        print(f"[API] Inserting prices into database, payload: {json.dumps(payload, indent=4)}")
        
        # Validate that payload contains required fields
        if isinstance(payload, dict):
            instrument_id = payload.get('instrument_id')
            provider = payload.get('provider')
            print(f"[API] Payload validation - instrument_id: {instrument_id}, provider: {provider}")
            
            if not instrument_id:
                print(f"[API] WARNING: payload missing instrument_id")
            if not provider:
                print(f"[API] WARNING: payload missing provider")
        elif isinstance(payload, list):
            print(f"[API] Payload is a list with {len(payload)} items")
            for i, item in enumerate(payload[:3]):  # Log first 3 items
                if isinstance(item, dict):
                    print(f"[API] Item {i}: instrument_id={item.get('instrument_id')}, provider={item.get('provider')}")
        
        row_id = db.insert_prices(payload)
        try:
            new_p = Prices.from_data(payload if isinstance(payload, list) else [payload])
            for iid, price in new_p.items():
                prices[iid] = price
        except Exception as upd_err:
            logging.warning(f'[API] Could not update in-memory prices after insert: {upd_err}')
        return {'inserted_id': row_id}
    except Exception as e:
        print(f"[API] Error inserting prices: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get('/fetch_termsheet', tags=['Assets'], summary='Fetch one termsheet PDF by instrument_id')
async def fetch_termsheet(instrument_id: str):
    if not instrument_id or not instrument_id.strip():
        raise HTTPException(status_code=400, detail='instrument_id is required')

    safe_id = Path(instrument_id.strip()).name
    blob_name = f"{safe_id}.pdf"

    # Try Azure Blob Storage first
    if AZURE_STORAGE_CONNECTION_STRING and AZURE_CONTAINER_NAME:
        try:
            from azure.storage.blob import BlobServiceClient
            from fastapi.responses import StreamingResponse
            client = BlobServiceClient.from_connection_string(AZURE_STORAGE_CONNECTION_STRING)
            blob_client = client.get_blob_client(container=AZURE_CONTAINER_NAME, blob=blob_name)
            stream = blob_client.download_blob()
            data = stream.readall()
            return StreamingResponse(
                iter([data]),
                media_type='application/pdf',
                headers={'Content-Disposition': f'inline; filename="{blob_name}"'},
            )
        except Exception as e:
            print(f'[Azure] fetch_termsheet blob miss for {blob_name}: {e} — falling back to local')

    # Fallback: local termsheets/ directory
    pdf_path = TERMSHEETS_DIR / blob_name
    if not pdf_path.exists():
        candidates = [p for p in TERMSHEETS_DIR.glob('*.pdf') if p.stem.lower() == safe_id.lower()]
        if candidates:
            pdf_path = candidates[0]
        else:
            raise HTTPException(status_code=404, detail=f'Termsheet not found for instrument_id: {safe_id}')

    return FileResponse(
        path=str(pdf_path),
        media_type='application/pdf',
        headers={'Content-Disposition': f'inline; filename="{pdf_path.name}"'},
    )


@app.get('/fetch_report', tags=['Assets'], summary='Fetch one output report PDF by instrument_id')
async def fetch_report(instrument_id: str):
    if not instrument_id or not instrument_id.strip():
        raise HTTPException(status_code=400, detail='instrument_id is required')

    safe_id = Path(instrument_id.strip()).name
    pdf_path = OUTPUT_DIR / f"{safe_id}.pdf"
    if not pdf_path.exists():
        # fallback: try case-insensitive lookup
        candidates = [p for p in OUTPUT_DIR.glob('*.pdf') if p.stem.lower() == safe_id.lower()]
        if candidates:
            pdf_path = candidates[0]
        else:
            raise HTTPException(status_code=404, detail=f'Report not found for instrument_id: {safe_id}')

    return FileResponse(
        path=str(pdf_path),
        media_type='application/pdf',
        headers={"Content-Disposition": f'inline; filename="{pdf_path.name}"'},
    )


@app.post('/price', tags=['Pricing'], summary='Price one instrument by instrument_id')
async def price(payload: PriceRequest):
    """Price a single bond.

    Expects: { "instrument_id": "FR0013398757" }
    Looks up the instrument in the cached assets list and prices it via price_asset().
    Returns the pricer result JSON.
    """
    instr = payload.instrument_id
    print(f"[/price] Request received for instrument_id={instr}")

    log_path = PROJECT_ROOT / 'output' / 'price_requests.log'
    log_path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        'ts': datetime.utcnow().isoformat() + 'Z',
        'client': None,
        'instrument': instr,
        'status': None,
        'msg': None,
    }

    # Look up the asset in the cached global assets list
    print(f"[/price] Assets cache size: {len(assets)}, keys: {list(assets.keys())[:10]}")
    _asset = assets.get(instr)
    
    # if not found, try underlying_assets cache
    if _asset is None:
        underlying = underlying_assets.get(instr)
        if underlying is not None:
            _asset = underlying  # Keep as Asset object, not dict
            print(f"[/price] Found asset in underlying_assets for instrument_id={instr}")
    
    # if not found, try DB lookup
    if _asset is None:
        try:
            _asset = db.select_asset(instr)
            if _asset is not None:
                print(f"[/price] Found asset in DB for instrument_id={instr}")
        except Exception as e:
            print(f"[/price] DB lookup failed for {instr}: {e}")
    
    if _asset is None:
        print(f"[/price] Asset not found in cache for instrument_id={instr}")
        entry['status'] = 404
        entry['msg'] = f'Asset not found in cache: {instr}'
        _write_log(log_path, {**entry, 'event': 'not_found'})
        raise HTTPException(status_code=404, detail=f'Asset not found for instrument_id: {instr}')
    print(f"[/price] Found asset in cache: {list(_asset.keys()) if isinstance(_asset, dict) else type(_asset)}")

    # Load curves
    print(f"[/price] Loading curve from {pricer.DEFAULT_CURVE_FILE}")
    curve_path = pricer.resolve_curve_path(str(pricer.DEFAULT_CURVE_FILE))
    try:
        curve_json = helper.load_json(curve_path)
        print(f"[/price] Curve loaded successfully from {curve_path}")
    except Exception as e:
        print(f"[/price] ERROR loading curve: {type(e).__name__}: {e}")
        raise HTTPException(status_code=500, detail=f'Could not load curve file: {e}')


    args = SimpleNamespace(
        issuer_spread_bp=None,
        tree_steps=None,
        time_steps=None,
        num_paths=None,
        seed=None,
    )

    # Price the asset
    print(f"[/price] Calling price_asset for {instr} with model={_asset.model}")
    asset_data=_asset.to_dict()
    import json as _json
    print(f"[/price] bond_data (full input):\n{_json.dumps(asset_data, indent=2, default=str)}")

    # Resolve credit_spread_bp from the CDS curve if not explicitly set on the asset
    _original_spread = _asset.credit_spread_bp
    _spread_resolved = False
    if _asset.credit_spread_bp is None:
        cds_curve_name = asset_data.get('cds_curve')
        if cds_curve_name:
            cds_curve_obj = curves.get(cds_curve_name)
            if cds_curve_obj and cds_curve_obj.tenors:
                tenor_obj = cds_curve_obj.get_tenor('5Y') or cds_curve_obj.tenors[0]
                _asset.credit_spread_bp = tenor_obj.rate
                _spread_resolved = True
                print(f"[/price] Resolved credit_spread_bp={tenor_obj.rate} bp from cds_curve={cds_curve_name}")
            else:
                print(f"[/price] cds_curve={cds_curve_name} not found in curves cache — credit_spread_bp stays None")

    try:
        result = pricer.price_asset(_asset, curve_json, args)
        print(f"[/price] Pricing succeeded for {instr}: {list(result.keys()) if isinstance(result, dict) else result}")

        # Store the pricing result in the database
        try:
            db.insert_prices(result)
            print(f"[/price] Stored pricing result in database for {instr}")
        except Exception as db_err:
            print(f"[/price] Warning: could not store pricing result in DB: {db_err}")

        # Update in-memory prices so /prices serves the new result immediately
        try:
            prices[instr] = Price.from_dict(result)
            print(f"[/price] Updated in-memory prices for {instr}")
        except Exception as upd_err:
            logging.warning(f"[/price] Could not update in-memory prices: {upd_err}")

        entry['status'] = 200
        entry['msg'] = 'pricing_succeeded'
        _write_log(log_path, {**entry, 'event': 'pricing_succeeded'})
        return result
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        print(f"[/price] ERROR pricing {instr}: {type(e).__name__}: {e}\n{tb}")
        entry['status'] = 500
        entry['msg'] = f'Could not price instrument: {e}'
        _write_log(log_path, {**entry, 'event': 'pricing_failed', 'error': str(e), 'traceback': tb})
        raise HTTPException(status_code=500, detail=f'Could not price instrument {instr}: {e}')
    finally:
        # Restore original spread so the cached Asset object is not mutated permanently
        if _spread_resolved:
            _asset.credit_spread_bp = _original_spread

@app.get('/jobs/{job_id}', tags=['Jobs'], summary='Get async job status')
async def get_job(job_id: str):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail='Job not found')
    # return a safe subset
    safe = {k: job.get(k) for k in ['id', 'status', 'cmd', 'created_ts', 'start_ts', 'end_ts', 'returncode', 'error', 'result_count']}
    return safe


@app.get('/prices', tags=['Pricing'], summary='Get all prices from in-memory cache')
async def get_prices(request: Request):
    """Return all prices from the in-memory global prices variable."""
    log_path = PROJECT_ROOT / 'output' / 'prices_access.log'
    log_path.parent.mkdir(parents=True, exist_ok=True)

    client = None
    try:
        client = request.client.host if request.client else None
    except Exception:
        pass

    try:
        data = prices.to_list()
        entry = {
            'ts': datetime.utcnow().isoformat() + 'Z',
            'client': client,
            'path': str(request.url.path),
            'status': 200,
            'msg': f'served {len(data)} entries',
        }
        try:
            with open(log_path, 'a') as lf:
                lf.write(json.dumps(entry) + '\n')
        except Exception:
            pass
        print(f"[API] /prices - 200 OK - served to {client}")
        return data
    except Exception as e:
        print(f"[API] /prices - 500 - {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get('/fetch_underlying_assets', tags=['Assets'], summary='Fetch all underlying assets')
async def fetch_underlying_assets():
    try:
        result = []
        for asset in underlying_assets.values():
            if hasattr(asset, 'to_dict'):
                item = asset.to_dict()
            elif isinstance(asset, dict):
                item = dict(asset)
            else:
                continue

            # Append last close + date from the in-memory time series
            ts = getattr(asset, 'ts', None)
            if ts is not None and len(ts) > 0:
                last_date = max(ts.keys())
                last_bar = ts[last_date]
                item['last_close'] = last_bar.close
                item['last_close_date'] = last_date
            else:
                item['last_close'] = None
                item['last_close_date'] = None

            result.append(item)

        print(f"[API] Returning {len(result)} underlying assets")
        out_path = PROJECT_ROOT / 'output' / 'fetch_underlying_assets.json'
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, 'w') as f:
            json.dump(result, f, indent=2, default=str)
        return FileResponse(
            path=str(out_path),
            media_type='application/json',
            headers={"Content-Disposition": f'attachment; filename="fetch_underlying_assets.json"'},
        )
    except Exception as e:
        print(f"[API] Error fetching underlying assets: {e}")
        raise HTTPException(status_code=500, detail='Could not fetch underlying assets')


@app.get('/fetch_noprice_assets', tags=['Assets'], summary='List asset instrument_ids not present in prices cache')
async def fetch_noprice_assets():
    priced_ids = set(prices.keys())
    missing_ids = [iid for iid in assets.keys() if iid not in priced_ids]
    return {
        'missing_instrument_ids': missing_ids,
        'count': len(missing_ids),
    }


@app.post('/price_all', tags=['Pricing'], summary='Start async pricing for all instruments')
async def price_all(request: Request):
    """Trigger pricing for all bonds by running `pricer.py --bond all` and return the generated prices.json."""
    log_path = PROJECT_ROOT / 'output' / 'price_requests.log'
    log_path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        'ts': datetime.utcnow().isoformat() + 'Z',
        'client': None,
        'status': None,
        'msg': None,
        'event': 'price_all'
    }
    try:
        entry['client'] = request.client.host if request.client else None
    except Exception:
        entry['client'] = None

    # log incoming
    try:
        with open(log_path, 'a') as lf:
            lf.write(json.dumps({**entry, 'phase': 'incoming'}) + '\n')
    except Exception:
        pass

    # Run pricer CLI in background thread and return a job id for polling
    pricer_py = PROJECT_ROOT / 'pricer.py'
    cmd = [sys.executable, str(pricer_py), '--bond', 'all']

    job_id = uuid.uuid4().hex
    job = {
        'id': job_id,
        'status': 'pending',
        'cmd': ' '.join(cmd),
        'created_ts': datetime.utcnow().isoformat() + 'Z',
        'start_ts': None,
        'end_ts': None,
        'stdout': None,
        'stderr': None,
        'returncode': None,
        'error': None,
    }
    with JOBS_LOCK:
        JOBS[job_id] = job

    try:
        _write_log(log_path, {**entry, 'phase': 'enqueued', 'job': job_id, 'cmd': job['cmd'] })
    except Exception:
        pass

    t = threading.Thread(target=_run_price_all, args=(job_id, cmd, log_path), daemon=True)
    t.start()

    return JSONResponse(status_code=202, content={'job_id': job_id, 'status_url': f'/jobs/{job_id}'})


def _run_update_curve(job_id: str, cmd: list, log_path: Path):
    with JOBS_LOCK:
        JOBS[job_id]['status'] = 'running'
        JOBS[job_id]['start_ts'] = datetime.utcnow().isoformat() + 'Z'
    _write_log(log_path, { 'ts': datetime.utcnow().isoformat() + 'Z', 'event': 'update_curve', 'job': job_id, 'phase': 'started', 'cmd': ' '.join(cmd) })
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
        stdout = proc.stdout or ''
        stderr = proc.stderr or ''
        with JOBS_LOCK:
            JOBS[job_id]['stdout'] = stdout[:500000]
            JOBS[job_id]['stderr'] = stderr[:500000]
            JOBS[job_id]['returncode'] = proc.returncode
    except Exception as e:
        with JOBS_LOCK:
            JOBS[job_id]['status'] = 'failed'
            JOBS[job_id]['end_ts'] = datetime.utcnow().isoformat() + 'Z'
            JOBS[job_id]['error'] = str(e)
        _write_log(log_path, { 'ts': datetime.utcnow().isoformat() + 'Z', 'event': 'update_curve', 'job': job_id, 'phase': 'failed', 'error': str(e) })
        return

    with JOBS_LOCK:
        JOBS[job_id]['status'] = 'succeeded'
        JOBS[job_id]['end_ts'] = datetime.utcnow().isoformat() + 'Z'
    _write_log(log_path, { 'ts': datetime.utcnow().isoformat() + 'Z', 'event': 'update_curve', 'job': job_id, 'phase': 'succeeded', 'stdout': stdout[:2000] })

    # Reload in-memory curves from DB (scripts already wrote to DB directly)
    global curves
    curves = _load_curves()
    print(f'[API] Reloaded {len(curves)} curves after update_curves job {job_id}')


@app.post('/update_curves', tags=['General'], summary='Start async swap curve update (ECB)')
async def update_curves(request: Request, payload: dict = None):
    """Trigger swap curve update by running `scripts/update_curve.py` in background and return a job id."""
    log_path = PROJECT_ROOT / 'output' / 'update_curves.log'
    log_path.parent.mkdir(parents=True, exist_ok=True)

    # read optional body
    raw_body = None
    try:
        raw_body = await request.body()
    except Exception:
        raw_body = None
    if payload is None and raw_body:
        try:
            payload = json.loads(raw_body.decode('utf-8'))
        except Exception:
            payload = None

    curve_file = None
    try:
        if isinstance(payload, dict):
            curve_file = payload.get('curve_file') or payload.get('curve')
    except Exception:
        curve_file = None

    # also allow query param ?curve_file=...
    try:
        q = dict(request.query_params)
        if 'curve_file' in q and q.get('curve_file'):
            curve_file = q.get('curve_file')
    except Exception:
        pass

    script_path = PROJECT_ROOT / 'scripts' / 'update_curves.py'
    cmd = [sys.executable, str(script_path)]
    if curve_file:
        cmd += ['--curve-file', str(curve_file)]

    job_id = uuid.uuid4().hex
    job = {
        'id': job_id,
        'status': 'pending',
        'cmd': ' '.join(cmd),
        'created_ts': datetime.utcnow().isoformat() + 'Z',
        'start_ts': None,
        'end_ts': None,
        'stdout': None,
        'stderr': None,
        'returncode': None,
        'error': None,
    }
    with JOBS_LOCK:
        JOBS[job_id] = job

    try:
        _write_log(log_path, {'phase': 'enqueued', 'job': job_id, 'cmd': job['cmd'], 'ts': datetime.utcnow().isoformat() + 'Z'})
    except Exception:
        pass

    t = threading.Thread(target=_run_update_curve, args=(job_id, cmd, log_path), daemon=True)
    t.start()

    return JSONResponse(status_code=202, content={'job_id': job_id, 'status_url': f'/jobs/{job_id}'})


# ---------------------------------------------------------------------------
# Auth endpoints
# ---------------------------------------------------------------------------

class RegisterPayload(BaseModel):
    email: str
    firstname: str
    lastname: str
    password: str

class LoginPayload(BaseModel):
    email: str
    password: str


@app.get('/web_sources', tags=['Assets'], summary='List available web sources for asset scraping')
def list_web_sources():
    return {key: url for key, url in WEB_SOURCES.items()}


# ---------------------------------------------------------------------------
# Shared scraping helpers
# ---------------------------------------------------------------------------

def _scrape_fetch(url: str, extra_headers: dict = None) -> requests.Response:
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9,it;q=0.8',
    }
    if extra_headers:
        headers.update(extra_headers)
    return requests.get(url, headers=headers, timeout=20)


def _parse_date_any(s: str) -> str:
    s = s.strip()
    from datetime import datetime as _dt
    for fmt in ('%d/%m/%Y', '%d-%m-%Y', '%Y-%m-%d', '%d.%m.%Y', '%b %d, %Y', '%d %b %Y', '%B %d, %Y'):
        try:
            return _dt.strptime(s, fmt).strftime('%Y-%m-%d')
        except ValueError:
            pass
    return s


def _parse_number_any(s: str):
    s = s.strip().replace('\xa0', '').replace(' ', '').replace(' ', '')
    for cleaned in (s.replace('.', '').replace(',', '.'), s.replace(',', '')):
        try:
            v = float(cleaned)
            return int(v) if v == int(v) else v
        except ValueError:
            pass
    return s


def _parse_rate_any(s: str):
    s = s.strip().replace(',', '.').replace('%', '').strip()
    try:
        v = float(s)
        return round(v / 100.0, 8) if v > 1.5 else round(v, 8)
    except ValueError:
        return s


def _extract_kv(soup) -> dict:
    raw = {}
    for table in soup.find_all('table'):
        for row in table.find_all('tr'):
            cells = row.find_all(['td', 'th'])
            if len(cells) >= 2:
                label = cells[0].get_text(' ', strip=True).lower().strip(':').strip()
                value = cells[1].get_text(' ', strip=True)
                if label and value and value not in ('-', 'N/A', 'n/a', ''):
                    raw.setdefault(label, value)
    for dl in soup.find_all('dl'):
        for dt, dd in zip(dl.find_all('dt'), dl.find_all('dd')):
            label = dt.get_text(' ', strip=True).lower().strip(':').strip()
            value = dd.get_text(' ', strip=True)
            if label and value and value not in ('-', 'N/A', ''):
                raw.setdefault(label, value)
    return raw


def _build_bond_json(isin: str, mapped: dict, last_price=None, default_market: str = 'MOT') -> dict:
    from datetime import date as _date
    today_str = _date.today().strftime('%Y-%m-%d')
    denomination = mapped.get('denomination', isin)
    maturity_date = mapped.get('maturity_date', '')
    issue_date = mapped.get('issue_date', '')
    first_coupon_date = mapped.get('interest_commencement_date', issue_date)
    fixed_coupon_rate = mapped.get('fixed_coupon_rate', 0.0)
    coupon_frequency = mapped.get('coupon_frequency', 'Annual')
    _freq_div = {'Annual': 1, 'Semiannual': 2, 'Quarterly': 4, 'Monthly': 12, 'Bimonthly': 6}
    periodic_coupon_rate = (
        round(fixed_coupon_rate / _freq_div.get(coupon_frequency, 1), 8)
        if isinstance(fixed_coupon_rate, float) else None
    )
    result = {
        'par': mapped.get('par', 100),
        'isin': isin,
        'model': 'bond',
        'issuer': mapped.get('issuer', ''),
        'market': mapped.get('market', default_market),
        'calendar': 'TARGET',
        'currency': mapped.get('currency', 'EUR'),
        'lot_size': mapped.get('lot_size', 1),
        'typology': mapped.get('typology', ''),
        'guarantor': mapped.get('guarantor') or None,
        'seniority': mapped.get('seniority', ''),
        'asset_type': 'bond',
        'issue_date': issue_date,
        'redemption': 100,
        'description': denomination,
        'expiry_date': maturity_date,
        'outstanding': mapped.get('outstanding'),
        'denomination': denomination,
        'float_spread': None,
        'target_price': 100,
        'trading_type': 'Clean',
        'instrument_id': isin,
        'maturity_date': maturity_date,
        'bond_structure': 'Plain Vanilla',
        'date_generation': 'Forward',
        'evaluation_date': today_str,
        'coupon_frequency': coupon_frequency,
        'coupon_structure': mapped.get('coupon_structure', 'fixed'),
        'credit_spread_bp': None,
        'accrual_day_count': mapped.get('accrual_day_count', 'Actual/Actual (Period Basis)'),
        'first_coupon_date': first_coupon_date,
        'fixed_coupon_rate': fixed_coupon_rate,
        'annual_coupon_rate': fixed_coupon_rate,
        'clearing_settlement': mapped.get('clearing_settlement', ''),
        'settlement_currency': mapped.get('currency', 'EUR'),
        'day_count_convention': mapped.get('accrual_day_count', 'ACT/ACT (PERIODIC BASIS)'),
        'first_day_of_trading': mapped.get('first_day_of_trading', ''),
        'negotiation_currency': mapped.get('currency', 'EUR'),
        'periodic_coupon_rate': periodic_coupon_rate,
        'business_day_convention': 'Unadjusted',
        'interest_commencement_date': first_coupon_date,
        '_code': isin,
    }
    if last_price is not None:
        result['last_price'] = last_price
    return result


# ---------------------------------------------------------------------------
# Source-specific scrapers
# ---------------------------------------------------------------------------

def _scrape_borsa_italiana(isin: str, soup) -> tuple:
    """Return (mapped_dict, last_price) from a Borsa Italiana scheda page."""

    def _map_freq(s):
        return {'semestrale': 'Semiannual', 'annuale': 'Annual', 'annua': 'Annual',
                'trimestrale': 'Quarterly', 'mensile': 'Monthly', 'bimestrale': 'Bimonthly'}.get(s.strip().lower(), s)

    def _map_coupon(s):
        return {'fisso': 'fixed', 'variabile': 'floating', 'zero coupon': 'zero_coupon', 'misto': 'mixed'}.get(s.strip().lower(), 'fixed')

    def _map_dc(s):
        return {'act/act': 'Actual/Actual (Period Basis)', 'act/365': 'Actual365Fixed',
                '30/360': '30/360', '30e/360': '30E/360'}.get(s.strip().upper(), s)

    LABEL_MAP = {
        'nome': ('denomination', str), 'denominazione': ('denomination', str),
        'isin': ('isin', str), 'emittente': ('issuer', str),
        'data di emissione': ('issue_date', _parse_date_any), 'data emissione': ('issue_date', _parse_date_any),
        'data di scadenza': ('maturity_date', _parse_date_any), 'scadenza': ('maturity_date', _parse_date_any),
        'data di godimento': ('interest_commencement_date', _parse_date_any),
        'godimento': ('interest_commencement_date', _parse_date_any),
        'primo giorno di negoziazione': ('first_day_of_trading', _parse_date_any),
        'cedola': ('fixed_coupon_rate', _parse_rate_any), 'tasso cedola': ('fixed_coupon_rate', _parse_rate_any),
        'tasso': ('fixed_coupon_rate', _parse_rate_any),
        'frequenza cedola': ('coupon_frequency', _map_freq), 'frequenza': ('coupon_frequency', _map_freq),
        'taglio minimo': ('lot_size', _parse_number_any), 'lotto minimo': ('lot_size', _parse_number_any),
        'valore nominale': ('par', _parse_number_any),
        'ammontare in circolazione': ('outstanding', _parse_number_any), 'outstanding': ('outstanding', _parse_number_any),
        'valuta': ('currency', str),
        'convenzione calcolo interessi': ('accrual_day_count', _map_dc),
        'convenzione di calcolo': ('accrual_day_count', _map_dc),
        'mercato': ('market', str),
        'clearing': ('clearing_settlement', str), 'clearing / settlement': ('clearing_settlement', str),
        'tipo cedola': ('coupon_structure', _map_coupon), 'tipo di cedola': ('coupon_structure', _map_coupon),
        'garanzia': ('guarantor', str), 'garante': ('guarantor', str),
        'prezzo di emissione': ('issue_price', _parse_number_any), 'prezzo emissione': ('issue_price', _parse_number_any),
        'seniority': ('seniority', str), 'tipologia': ('typology', str), 'tipo': ('typology', str),
        # last price variants
        'ultimo': ('last_price', _parse_number_any),
        'ultimo prezzo': ('last_price', _parse_number_any),
        'prezzo': ('last_price', _parse_number_any),
        'quotazione': ('last_price', _parse_number_any),
    }

    raw = _extract_kv(soup)

    # Also scan standalone price box (e.g. <span class="price">, header price areas)
    for tag in soup.find_all(['span', 'div', 'strong', 'b'],
                              class_=lambda c: c and any(x in c.lower() for x in ['price', 'prezzo', 'ultimo', 'quot'])):
        text = tag.get_text(strip=True).replace(',', '.')
        try:
            v = float(text)
            if 10 <= v <= 200:
                raw.setdefault('ultimo', tag.get_text(strip=True))
                break
        except ValueError:
            pass

    mapped = {}
    for label, value in raw.items():
        if label in LABEL_MAP:
            key, fn = LABEL_MAP[label]
            try:
                mapped[key] = fn(value)
            except Exception:
                mapped[key] = value

    last_price = mapped.pop('last_price', None)
    return mapped, last_price




# ---------------------------------------------------------------------------
# Main endpoint
# ---------------------------------------------------------------------------

@app.get('/fetch_asset_web', tags=['Assets'], summary='Scrape bond data from a registered web source and save it')
async def fetch_asset_web(isin: str, source: str = 'borsa_italiana_mot'):
    from bs4 import BeautifulSoup
    import asyncio

    if source not in WEB_SOURCES:
        raise HTTPException(status_code=400, detail=f"Unknown source '{source}'. Available: {list(WEB_SOURCES.keys())}")

    isin = isin.strip().upper()
    url = WEB_SOURCES[source].format(isin=isin)

    print(f'[fetch_asset_web] GET {url}')
    loop = asyncio.get_event_loop()
    try:
        resp = await loop.run_in_executor(None, lambda: _scrape_fetch(url))
    except requests.exceptions.ConnectionError as e:
        raise HTTPException(status_code=502, detail=f'Connection error: {e} — URL: {url}')
    except requests.exceptions.Timeout:
        raise HTTPException(status_code=502, detail=f'Timeout — URL: {url}')
    except Exception as e:
        raise HTTPException(status_code=502, detail=f'Unexpected error: {e} — URL: {url}')

    print(f'[fetch_asset_web] HTTP {resp.status_code} — {url}')
    if resp.status_code == 404:
        raise HTTPException(status_code=404, detail=f'ISIN {isin} not found on {source} (HTTP 404) — URL: {url}')
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail=f'{source} returned HTTP {resp.status_code} — URL: {url}')

    soup = BeautifulSoup(resp.text, 'lxml')

    if source.startswith('borsa_italiana'):
        mapped, last_price = _scrape_borsa_italiana(isin, soup)
        default_market = 'MOT'
    else:
        raise HTTPException(status_code=400, detail=f"No scraper implemented for source '{source}'.")

    result = _build_bond_json(isin, mapped, last_price=last_price, default_market=default_market)

    if not result.get('maturity_date'):
        raise HTTPException(status_code=422, detail=f'Could not parse bond data for {isin} — check the ISIN or page structure. URL: {url}')

    saved = await _save_asset_data(result)
    print(f'[fetch_asset_web] Saved {isin} from {source}: {saved}')
    return result


@app.get('/users', tags=['General'], summary='List all registered users (passwords omitted)')
async def list_users():
    return [
        {k: v for k, v in u.items() if k != 'password'}
        for u in users.values()
    ]


@app.post('/register', tags=['General'], summary='Register a new user')
async def register_user(payload: RegisterPayload):
    email = payload.email.strip().lower()
    if email in users:
        raise HTTPException(status_code=409, detail='A user with this email already exists.')
    password_hash = bcrypt.hashpw(payload.password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
    try:
        db.insert_user(email, payload.firstname.strip(), payload.lastname.strip(), password_hash)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Could not save user: {e}')
    users[email] = {
        'email': email,
        'firstname': payload.firstname.strip(),
        'lastname': payload.lastname.strip(),
        'password': password_hash,
    }
    return {'status': 'ok', 'email': email}


@app.post('/login', tags=['General'], summary='Authenticate a user')
async def login_user(payload: LoginPayload):
    email = payload.email.strip().lower()
    user = users.get(email)
    if not user:
        raise HTTPException(status_code=401, detail='Invalid email or password.')
    stored_hash = user['password']
    if not bcrypt.checkpw(payload.password.encode('utf-8'), stored_hash.encode('utf-8')):
        raise HTTPException(status_code=401, detail='Invalid email or password.')
    return {
        'status': 'ok',
        'email': user['email'],
        'firstname': user['firstname'],
        'lastname': user['lastname'],
    }
