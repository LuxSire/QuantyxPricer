import sys
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
from fastapi.responses import JSONResponse, RedirectResponse, FileResponse
from classes import  Prices, Asset, Assets,TS_Dict
from models import helper
import db
import provider
import pricer
from pydantic import BaseModel

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
    ],
)

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
TERMSHEETS_DIR: Path = PROJECT_ROOT / 'termsheets'
TERMSHEETS_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR: Path = PROJECT_ROOT / 'output'
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Simple in-memory job registry for background tasks (non-persistent)
JOBS = {}
JOBS_LOCK = threading.Lock()

# Cached assets and prices loaded at startup
assets = Assets()
prices = Prices()
underlying_assets = Assets()


@app.on_event('startup')
async def initialize_data():
    global assets, prices,underlying_assets
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
            asset.prices=prices.get(asset.instrument_id)  # Attach prices if available
            if hasattr(asset, 'underlying') and asset.underlying is not None:
                # asset.underlying is already an Asset instance created by Asset.from_dict()
                key = asset.underlying.instrument_id
                asset.underlying_ts = timeseries.get(key)  # Attach underlying's prices (was incorrectly using asset.instrument_id)
                print(f"[API] initialize_data: asset={asset.instrument_id} underlying_prices_in_db={timeseries.get(key) is not None}")
                if key:
                    underlying_assets[key] = asset.underlying
                    underlying_assets[key].ts = asset.underlying_ts  # Attach prices if available
                    _ts = underlying_assets[key].ts
                    _vol = _ts.volatility() if _ts is not None else 'N/A'
                    print(f"[API] initialize_data: underlying_asset={key} prices_set={_ts is not None} volatility={_vol}")
            print(f"[API] Extracted {len(underlying_assets)} underlying assets")
    except Exception as e:
        print(f"[API] Warning: could not extract underlying assets: {e}")
        underlying_assets = Assets()

def _write_log(log_path, obj):
    try:
        with open(log_path, 'a') as lf:
            lf.write(json.dumps(obj) + '\n')
    except Exception:
        pass


def _run_price_all(job_id: str, cmd: list, log_path: Path):
    with JOBS_LOCK:
        JOBS[job_id]['status'] = 'running'
        JOBS[job_id]['start_ts'] = datetime.utcnow().isoformat() + 'Z'
    _write_log(log_path, { 'ts': datetime.utcnow().isoformat() + 'Z', 'event': 'price_all', 'job': job_id, 'phase': 'started', 'cmd': ' '.join(cmd) })
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
        stdout = proc.stdout or ''
        stderr = proc.stderr or ''
        # write stdout/stderr to job
        with JOBS_LOCK:
            JOBS[job_id]['stdout'] = stdout[:500000]
            JOBS[job_id]['stderr'] = stderr[:500000]
            JOBS[job_id]['returncode'] = proc.returncode
    except Exception as e:
        with JOBS_LOCK:
            JOBS[job_id]['status'] = 'failed'
            JOBS[job_id]['end_ts'] = datetime.utcnow().isoformat() + 'Z'
            JOBS[job_id]['error'] = str(e)
        _write_log(log_path, { 'ts': datetime.utcnow().isoformat() + 'Z', 'event': 'price_all', 'job': job_id, 'phase': 'failed', 'error': str(e) })
        return

    # after running, try to read output/prices.json
    out_path = PROJECT_ROOT / 'output' / 'prices.json'
    if out_path.exists() and (JOBS.get(job_id) is not None):
        try:
            with open(out_path, 'r') as f:
                data = json.load(f)
            with JOBS_LOCK:
                JOBS[job_id]['status'] = 'succeeded'
                JOBS[job_id]['end_ts'] = datetime.utcnow().isoformat() + 'Z'
                JOBS[job_id]['result_count'] = len(data) if isinstance(data, list) else None
            _write_log(log_path, { 'ts': datetime.utcnow().isoformat() + 'Z', 'event': 'price_all', 'job': job_id, 'phase': 'succeeded', 'stdout': stdout[:2000] })
        except Exception as e:
            with JOBS_LOCK:
                JOBS[job_id]['status'] = 'failed'
                JOBS[job_id]['end_ts'] = datetime.utcnow().isoformat() + 'Z'
                JOBS[job_id]['error'] = f'Could not read prices.json: {e}'
            _write_log(log_path, { 'ts': datetime.utcnow().isoformat() + 'Z', 'event': 'price_all', 'job': job_id, 'phase': 'read_failed', 'error': str(e) })
    else:
        with JOBS_LOCK:
            JOBS[job_id]['status'] = 'failed'
            JOBS[job_id]['end_ts'] = datetime.utcnow().isoformat() + 'Z'
            JOBS[job_id]['error'] = 'prices.json not produced'
        _write_log(log_path, { 'ts': datetime.utcnow().isoformat() + 'Z', 'event': 'price_all', 'job': job_id, 'phase': 'no_output', 'stdout': stdout[:2000] if 'stdout' in locals() else '' })


@app.get('/', include_in_schema=False)
async def root():
    return RedirectResponse(url='/docs')



@app.post('/assets', tags=['Assets'], summary='Upload an asset JSON file')
async def save_asset(request: Request, payload: dict = None):
    """Save a bond JSON into the assets/ folder.

    Body must be the bond JSON itself (object) and include `instrument_id` or `isin`.
    Returns the saved filename.
    """
    # Log incoming request for debugging
    try:
        raw_body = await request.body()
        print('\n[API] /assets received request headers:')
        for k, v in request.headers.items():
            print(f'  {k}: {v}')
        print(f'[API] Raw body length: {len(raw_body)}')
    except Exception as e:
        print(f'[API] Could not read raw request body: {e}')

    if payload is None:
        # Attempt to parse JSON from raw body for more helpful error messages
        try:
            payload = json.loads(raw_body.decode('utf-8')) if raw_body else None
        except Exception as e:
            raise HTTPException(status_code=400, detail=f'Invalid JSON body: {e}')

    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail='JSON object required in request body')
    asset = payload

    # Normalize common date fields into ISO YYYY-MM-DD
    def try_parse_date(val: Any):
        if not isinstance(val, str):
            return None
        s = val.strip()
        if not s:
            return None
        # Fast reject values that look like already ISO
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
    
    # Also save to MySQL database
    try:
        row_id = db.insert_asset(asset)
        print(f"[API] Inserted asset into MySQL with id={row_id}")
    except Exception as e:
        print(f"[API] Warning: could not insert asset into MySQL: {e}")
        # Do not fail the upload if DB insert fails; file is already saved
    
    return {"saved": filename, "path": str(path)}


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

    try:
        if str(PROJECT_ROOT) not in sys.path:
            sys.path.insert(0, str(PROJECT_ROOT))
        from scripts import termsheet_to_json as ts2j
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Could not load termsheet converter: {e}')

    try:
        # Let the converter derive output filename from detected ISIN (or PDF stem).
        ts2j.process_file(temp_pdf, ASSETS_DIR)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Termsheet conversion failed: {e}')

    # Try to discover generated JSON name from parsed ISIN, with fallback to PDF stem.
    try:
        text = ts2j.extract_text_from_pdf(temp_pdf)
        guessed = ts2j.heuristic_field_from_text(text)
        instrument_id = guessed.get('instrument_id') or temp_pdf.stem.split('_', 1)[-1]
    except Exception:
        instrument_id = temp_pdf.stem.split('_', 1)[-1]

    out_file = f"{instrument_id}.json"
    out_path = ASSETS_DIR / out_file
    if not out_path.exists():
        # fallback: converter may have used source stem
        fallback = ASSETS_DIR / f"{Path(filename).stem}.json"
        if fallback.exists():
            out_path = fallback
            out_file = fallback.name
        else:
            raise HTTPException(status_code=500, detail='Conversion finished but output JSON was not found')

    try:
        with open(out_path, 'r', encoding='utf-8') as f:
            payload = json.load(f)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Could not read generated JSON: {e}')

    return {
        'saved': out_file,
        'path': str(out_path),
        'instrument_id': payload.get('instrument_id') or payload.get('isin'),
        'asset': payload,
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

@app.get('/fetch_underlying_assets', tags=['Assets'], summary='Fetch all underlying assets')
async def fetch_underlying_assets():
    try:
        # Convert underlying_assets Assets collection to list of dicts
        result = []
        for asset in underlying_assets.values():
            if hasattr(asset, 'to_dict'):
                result.append(asset.to_dict())
            elif isinstance(asset, dict):
                result.append(asset)
        print(f"[API] Returning {len(result)} underlying assets")
        return result
    except Exception as e:
        print(f"[API] Error fetching underlying assets: {e}")
        raise HTTPException(status_code=500, detail='Could not fetch underlying assets')

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
        
        if res is None :
            raise HTTPException(status_code=404, detail=f'No market data found for {safe_id}')
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
        return {'inserted_id': row_id}
    except Exception as e:
        print(f"[API] Error inserting prices: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get('/fetch_termsheet', tags=['Assets'], summary='Fetch one termsheet PDF by instrument_id')
async def fetch_termsheet(instrument_id: str):
    if not instrument_id or not instrument_id.strip():
        raise HTTPException(status_code=400, detail='instrument_id is required')

    safe_id = Path(instrument_id.strip()).name
    pdf_path = TERMSHEETS_DIR / f"{safe_id}.pdf"
    if not pdf_path.exists():
        # fallback: try case-insensitive lookup
        candidates = [p for p in TERMSHEETS_DIR.glob('*.pdf') if p.stem.lower() == safe_id.lower()]
        if candidates:
            pdf_path = candidates[0]
        else:
            raise HTTPException(status_code=404, detail=f'Termsheet not found for instrument_id: {safe_id}')

    return FileResponse(
        path=str(pdf_path),
        media_type='application/pdf',
        headers={"Content-Disposition": f'inline; filename="{pdf_path.name}"'},
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
    try:
        result = pricer.price_asset(_asset, curve_json, args)
        print(f"[/price] Pricing succeeded for {instr}: {list(result.keys()) if isinstance(result, dict) else result}")
        
        # Store the pricing result in the database
        try:
            db.insert_prices(result)
            print(f"[/price] Stored pricing result in database for {instr}")
        except Exception as db_err:
            print(f"[/price] Warning: could not store pricing result in DB: {db_err}")
        
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

@app.get('/jobs/{job_id}', tags=['Jobs'], summary='Get async job status')
async def get_job(job_id: str):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail='Job not found')
    # return a safe subset
    safe = {k: job.get(k) for k in ['id', 'status', 'cmd', 'created_ts', 'start_ts', 'end_ts', 'returncode', 'error', 'result_count']}
    return safe


@app.get('/prices', tags=['Pricing'], summary='Get latest generated prices.json')
async def get_prices(request: Request):
    """Return the generated output/prices.json if present and log access attempts."""
    out_path = PROJECT_ROOT / 'output' / 'prices.json'
    log_path = PROJECT_ROOT / 'output' / 'prices_access.log'
    log_path.parent.mkdir(parents=True, exist_ok=True)

    entry = {
        'ts': datetime.utcnow().isoformat() + 'Z',
        'client': None,
        'path': str(request.url.path),
        'status': None,
        'msg': None,
    }
    try:
        entry['client'] = request.client.host if request.client else None
    except Exception:
        entry['client'] = None

    if not out_path.exists():
        entry['status'] = 404
        entry['msg'] = 'prices.json not found'
        try:
            with open(log_path, 'a') as lf:
                lf.write(json.dumps(entry) + '\n')
        except Exception:
            pass
        print(f"[API] /prices - 404 - {entry['msg']} - client={entry['client']}")
        raise HTTPException(status_code=404, detail='prices.json not found')

    try:
        with open(out_path, 'r') as f:
            data = json.load(f)
        entry['status'] = 200
        try:
            entry['msg'] = f"served {len(data)} entries" if isinstance(data, list) else 'served object'
        except Exception:
            entry['msg'] = 'served data'
        try:
            with open(log_path, 'a') as lf:
                lf.write(json.dumps(entry) + '\n')
        except Exception:
            pass
        print(f"[API] /prices - 200 OK - served to {entry['client']}")
        return data
    except Exception as e:
        entry['status'] = 500
        entry['msg'] = f'Could not read prices.json: {e}'
        try:
            with open(log_path, 'a') as lf:
                lf.write(json.dumps(entry) + '\n')
        except Exception:
            pass
        print(f"[API] /prices - 500 - {e}")
        raise HTTPException(status_code=500, detail=entry['msg'])


@app.get('/fetch_underlying_assets', tags=['Assets'], summary='Fetch all underlying assets')
async def fetch_underlying_assets():
    try:
        # Convert underlying_assets Assets collection to list of dicts
        result = []
        for asset in underlying_assets.values():
            if hasattr(asset, 'to_dict'):
                result.append(asset.to_dict())
            elif isinstance(asset, dict):
                result.append(asset)
        print(f"[API] Returning {len(result)} underlying assets")
        # Save to a JSON file and return it
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
