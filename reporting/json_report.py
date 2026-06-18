import json
from pathlib import Path
from datetime import datetime

BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent
OUTPUT_DIR = PROJECT_ROOT / 'output'


def create_json_report(reports, filename: str = 'prices.json'):
    """Write a JSON summary file containing `reports` to a fixed filename.

    The file will be written to the project's `output/` directory and will
    overwrite any existing `prices.json`.
    """
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_DIR / filename
    # Normalize each report so all models follow the Hull-White output template:
    # - ensure `model_ytm_to_maturity` exists (copy from `yield_to_maturity` when present)
    # - ensure `npv_to_worst_call`, `npv_to_first_call`, `npv_to_maturity` exist
    def _normalize_report(rep):
        if not isinstance(rep, dict):
            return rep
        out = dict(rep)
        result = out.get('result') or {}
        model_name = str(out.get('model', '')).strip().lower()

        # For SPIRE and CLN, keep canonical short names ytm/ytc.
        if model_name in {'spire', 'cln'}:
            if 'ytm' not in result:
                if 'model_ytm_to_maturity' in result:
                    result['ytm'] = result.get('model_ytm_to_maturity')
                elif 'yield_to_maturity' in result:
                    result['ytm'] = result.get('yield_to_maturity')
            if 'ytc' not in result:
                if 'model_ytc_to_first_call' in result:
                    result['ytc'] = result.get('model_ytc_to_first_call')
                elif 'yield_to_call' in result:
                    result['ytc'] = result.get('yield_to_call')
            result.pop('model_ytm_to_maturity', None)
            result.pop('model_ytc_to_first_call', None)
        else:
            # Keep model_* yield keys for non-SPIRE/non-CLN models.
            if 'model_ytm_to_maturity' not in result and 'yield_to_maturity' in result:
                result['model_ytm_to_maturity'] = result.get('yield_to_maturity')
            if 'model_ytm_to_maturity' not in result and 'ytm' in result:
                result['model_ytm_to_maturity'] = result.get('ytm')

            if 'model_ytc_to_first_call' not in result and 'yield_to_call' in result:
                result['model_ytc_to_first_call'] = result.get('yield_to_call')
            if 'model_ytc_to_first_call' not in result and 'ytc' in result:
                result['model_ytc_to_first_call'] = result.get('ytc')

        # Ensure NPVs exist: prefer explicit npv_to_* keys, fallback to selected_npv or npv_to_maturity
        sel = result.get('selected_npv')
        nm = result.get('npv_to_maturity') or sel
        nf = result.get('npv_to_first_call') or sel
        nw = result.get('npv_to_worst_call') or sel
        if nm is not None:
            result['npv_to_maturity'] = nm
        if nf is not None:
            result['npv_to_first_call'] = nf
        if nw is not None:
            result['npv_to_worst_call'] = nw

        out['result'] = result
        return out

    normalized = [_normalize_report(r) for r in reports]
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(normalized, f, indent=2, default=str, ensure_ascii=False)
    return out_path
