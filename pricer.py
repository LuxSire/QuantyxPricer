import argparse
import sys
from dataclasses import dataclass
from datetime import date as dt_date
from pathlib import Path
from typing import Callable, Optional
from classes import Asset

from models import hullwhite, index_linked, inflation_linked, montecarlo, spire, trinomialtree, cln, helper,at1
from models.convertible import barrier_convertible, barrier_discount, simple_convertible, autocallable_reverse_convertible
from models.swaps import irs, cds, cap, floor
try:
    from reporting import pdf_report, json_report
except ModuleNotFoundError:
    import reporting.pdf_report as pdf_report
    import reporting.json_report as json_report
from scripts.update_ecb import update_swap_curves_ecb
from scripts.update_fed import update_swap_curves_fed

sys.path.insert(0, str(Path(__file__).parent / 'scripts'))

PROJECT_ROOT = Path(__file__).resolve().parent
ASSETS_DIR = PROJECT_ROOT / 'assets'
CURVES_DIR = PROJECT_ROOT / 'curves'
DEFAULT_BOND_FILE = ASSETS_DIR / 'XS1693822634.json'
DEFAULT_CURVE_FILE = CURVES_DIR / 'swap_curves.json'


def parse_args():
    parser = argparse.ArgumentParser(description='Root pricer dispatcher based on bond JSON model field.')
    parser.add_argument('--bond', default=None, help='Bond filename/path, or use `all` to price every bond in assets/')
    parser.add_argument('--bond-file', default=str(DEFAULT_BOND_FILE), help='Bond JSON file path or filename in assets/')
    parser.add_argument('--curve-file', default=str(DEFAULT_CURVE_FILE), help='Curve JSON file path or filename in curves/')
    parser.add_argument('--all-bonds', action='store_true', help='Price all bond JSON files in assets/')
    parser.add_argument('--issuer-spread-bp', type=float, default=None, help='Optional override for tree/montecarlo issuer spread')
    parser.add_argument('--tree-steps', type=int, default=None, help='Optional override for trinomial tree steps')
    parser.add_argument('--time-steps', type=int, default=None, help='Optional override for montecarlo time steps')
    parser.add_argument('--num-paths', type=int, default=None, help='Optional override for montecarlo number of paths')
    parser.add_argument('--seed', type=int, default=None, help='Optional override for montecarlo random seed')
    return parser.parse_args()


def resolve_asset_path(path_like: str):
    path = Path(path_like)
    if path.is_absolute() and path.exists():
        return path
    if path.suffix == '':
        path = Path(f'{path_like}.json')
    candidates = [path, PROJECT_ROOT / path, ASSETS_DIR / path]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return path


def resolve_curve_path(path_like: str):
    path = Path(path_like)
    if path.is_absolute() and path.exists():
        return path
    candidates = [path, PROJECT_ROOT / path, CURVES_DIR / path]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return path


def expected_isin_filename(bond_data):
    instrument_id = str(bond_data.get('instrument_id', '')).strip()
    if instrument_id:
        return f'{instrument_id}.json'
    isin = str(bond_data.get('isin', '')).strip()
    if isin:
        return f'{isin}.json'
    return None


def validate_asset_filenames_by_isin():
    mismatches = []
    for bond_file in sorted(ASSETS_DIR.glob('*.json')):
        if bond_file.name.startswith('.'):
            continue
        try:
            bond_data = hullwhite.load_json(bond_file)
        except Exception as exc:
            print(f'Skipping {bond_file.name}: could not load JSON ({exc})')
            continue
        expected_name = expected_isin_filename(bond_data)
        if expected_name and bond_file.name != expected_name:
            mismatches.append((bond_file.name, expected_name))
    if mismatches:
        details = ', '.join([f'{a} -> {e}' for a, e in mismatches])
        raise ValueError(
            'Bond files in assets/ must be named with the ISIN (or instrument_id) as filename. '
            f'Mismatches: {details}'
        )


def apply_mc_overrides(bond_data, args):
    data = dict(bond_data)
    if getattr(args, 'time_steps', None) is not None:
        data['mc_time_steps'] = args.time_steps
    if getattr(args, 'num_paths', None) is not None:
        data['mc_num_paths'] = args.num_paths
    if getattr(args, 'seed', None) is not None:
        data['mc_seed'] = args.seed
    return data


# ---------------------------------------------------------------------------
# Model dispatch table
# ---------------------------------------------------------------------------

@dataclass
class _ModelSpec:
    price_fn: Callable        # (data, curve_json, args) -> result
    print_fn: Callable        # (data, result, curve_json) -> None
    model_key: str            # canonical name for pdf_report
    prep: Optional[Callable] = None  # (data, args) -> data — applied before price_fn


def _prep_mc(data, args):
    return apply_mc_overrides(data, args)


def _prep_tree(data, args):
    data = dict(data)
    if getattr(args, 'tree_steps', None) is not None:
        data['tree_time_steps'] = args.tree_steps
    return data


def _prep_simple_convertible(data, args):
    data = apply_mc_overrides(data, args)
    if 'convertible_type' not in data:
        mn = str(data.get('model', '')).strip().lower()
        data['convertible_type'] = 'reverse' if 'reverse' in mn else 'standard'
    return data


def _spread(args):
    return getattr(args, 'issuer_spread_bp', None)


_MODELS: dict[str, _ModelSpec] = {
    'hullwhite': _ModelSpec(
        price_fn=lambda d, cj, a: hullwhite.price_asset(d, cj),
        print_fn=lambda d, r, cj: hullwhite.print_report(d, r, curve_json=cj),
        model_key='hullwhite',
    ),
    'cln': _ModelSpec(
        price_fn=lambda d, cj, a: cln.price_asset(d, cj),
        print_fn=lambda d, r, cj: cln.print_report(d, r),
        model_key='cln',
    ),
    'at1': _ModelSpec(
        price_fn=lambda d, cj, a: at1.price_asset(d, cj),
        print_fn=lambda d, r, cj: at1.print_report(d, r),
        model_key='at1',
    ),
        'pik': _ModelSpec(
        price_fn=lambda d, cj, a: pik.price_asset(d, cj),
        print_fn=lambda d, r, cj: pik.print_report(d, r),
        model_key='pik',
    ),

    'autocallable_reverse_convertible': _ModelSpec(
        price_fn=lambda d, cj, a: autocallable_reverse_convertible.price_asset(d, cj),
        print_fn=lambda d, r, cj: autocallable_reverse_convertible.print_report(d, r),
        model_key='autocallable_reverse_convertible',
    ),
    'spire': _ModelSpec(
        price_fn=lambda d, cj, a: spire.price_asset(d, cj),
        print_fn=lambda d, r, cj: spire.print_report(d, r),
        model_key='spire',
    ),
    'index_linked': _ModelSpec(
        price_fn=lambda d, cj, a: index_linked.price_asset(d, cj),
        print_fn=lambda d, r, cj: index_linked.print_report(d, r),
        model_key='index_linked',
    ),
    'inflation_linked': _ModelSpec(
        price_fn=lambda d, cj, a: inflation_linked.price_asset(d, cj),
        print_fn=lambda d, r, cj: inflation_linked.print_result(d, r),
        model_key='inflation_linked',
    ),
    'trinomialtree': _ModelSpec(
        price_fn=lambda d, cj, a: trinomialtree.price_asset(d, cj, issuer_spread_bp=_spread(a)),
        print_fn=lambda d, r, cj: trinomialtree.print_tree_result(d, r),
        model_key='trinomialtree',
        prep=_prep_tree,
    ),
    'montecarlo': _ModelSpec(
        price_fn=lambda d, cj, a: montecarlo.price_asset(d, cj, issuer_spread_bp=_spread(a)),
        print_fn=lambda d, r, cj: montecarlo.print_mc_result(d, r),
        model_key='montecarlo',
        prep=_prep_mc,
    ),
    'barrier_convertible': _ModelSpec(
        price_fn=lambda d, cj, a: barrier_convertible.price_asset(d, cj, issuer_spread_bp=_spread(a)),
        print_fn=lambda d, r, cj: barrier_convertible.print_result(d, r),
        model_key='barrier_convertible',
        prep=_prep_mc,
    ),
    'barrier_discount': _ModelSpec(
        price_fn=lambda d, cj, a: barrier_discount.price_asset(d, cj, issuer_spread_bp=_spread(a)),
        print_fn=lambda d, r, cj: barrier_discount.print_result(d, r),
        model_key='barrier_discount',
        prep=_prep_mc,
    ),
    'simple_convertible': _ModelSpec(
        price_fn=lambda d, cj, a: simple_convertible.price_asset(d, cj, issuer_spread_bp=_spread(a)),
        print_fn=lambda d, r, cj: simple_convertible.print_result(d, r),
        model_key='simple_convertible',
        prep=_prep_simple_convertible,
    ),
    'irs': _ModelSpec(
        price_fn=lambda d, cj, a: irs.price_asset(d, cj),
        print_fn=lambda d, r, cj: irs.print_result(d, r),
        model_key='irs',
    ),
    'cds': _ModelSpec(
        price_fn=lambda d, cj, a: cds.price_asset(d, cj),
        print_fn=lambda d, r, cj: cds.print_result(d, r),
        model_key='cds',
    ),
    'cap': _ModelSpec(
        price_fn=lambda d, cj, a: cap.price_asset(d, cj),
        print_fn=lambda d, r, cj: cap.print_result(d, r),
        model_key='cap',
    ),
    'floor': _ModelSpec(
        price_fn=lambda d, cj, a: floor.price_asset(d, cj),
        print_fn=lambda d, r, cj: floor.print_result(d, r),
        model_key='floor',
    ),
}

# Aliases resolved before lookup
_ALIASES = {
    'bond': 'hullwhite',
    'barrier_reverse_convertible': 'barrier_convertible',
    'simple_reverse_convertible': 'simple_convertible',
}


def _run_model(model_name: str, data: dict, curve_json, args, instrument_id: str, bond_file_name: str) -> dict:
    canonical = _ALIASES.get(model_name, model_name)
    spec = _MODELS.get(canonical)
    if spec is None:
        supported = ', '.join(sorted(_MODELS) + sorted(_ALIASES))
        raise ValueError(
            f'Unsupported model="{model_name}" for asset {instrument_id}. '
            f'Supported values: {supported}.'
        )
    if spec.prep:
        data = spec.prep(data, args)
    result = spec.price_fn(data, curve_json, args)
    spec.print_fn(data, result, curve_json)
    pdf_path = pdf_report.create_pdf_report(
        model_name=spec.model_key,
        instrument_id=instrument_id,
        input_payload=data,
        output_payload=result,
    )
    print(f'PDF report: {pdf_path}')
    print()
    return {
        'bond_file': bond_file_name,
        'instrument_id': instrument_id,
        'model': model_name,
        'currency': data.get('currency'),
        'pdf': str(pdf_path),
        'result': result,
    }


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------

def price_asset(asset: Asset, curve_json: dict, args: argparse.Namespace) -> dict:
    """Price a single asset from an Asset object (called programmatically from the API)."""
    if asset.underlying_ts is not None:
        asset.underlying_volatility = asset.underlying_ts.volatility()
    asset_data = _normalize_dates(asset.to_dict())

    ul_list = getattr(asset, 'underlyings', None) or []
    for i, ul in enumerate(ul_list):
        ul_ts = getattr(ul, 'ts', None)
        if ul_ts is None:
            continue
        vol = ul_ts.volatility()
        if vol is None:
            continue
        if isinstance(asset_data.get('underlyings'), list) and i < len(asset_data['underlyings']):
            if isinstance(asset_data['underlyings'][i], dict):
                asset_data['underlyings'][i]['volatility'] = vol
        asset_data[f'underlying_volatility_{i}'] = vol

    asset_data['evaluation_date'] = dt_date.today().strftime('%d-%m-%Y')
    instrument_id = asset.instrument_id or 'unknown'
    pseudo_filename = f'{instrument_id}.json'

    model_name = str(asset_data.get('model', '')).strip().lower()
    if not model_name or model_name == 'none':
        asset_type = str(asset_data.get('asset_type', '')).strip().lower()
        if asset_type == 'equity':
            return {
                'bond_file': pseudo_filename,
                'instrument_id': instrument_id,
                'model': 'none',
                'currency': asset_data.get('currency'),
                'message': f'Equity asset {instrument_id} cannot be priced with bond pricing models.',
                'asset_type': 'equity',
            }
        raise ValueError(f'Missing model field for asset {instrument_id}. Add model in the Asset object.')

    return _run_model(model_name, asset_data, curve_json, args, instrument_id, pseudo_filename)


def dispatch_one(bond_file: Path, curve_json, args):
    """Price a single asset from a JSON file path."""
    bond_data = helper.load_json(bond_file)

    expected_name = expected_isin_filename(bond_data)
    if expected_name and bond_file.name != expected_name:
        raise ValueError(
            f'Bond file name must match ISIN/instrument_id. Got {bond_file.name}, expected {expected_name}.'
        )

    model_name = str(bond_data.get('model', '')).strip().lower()
    if not model_name:
        raise ValueError(f'Missing model field in {bond_file.name}. Add model in bond JSON.')

    instrument_id = bond_data.get('instrument_id', 'unknown')
    return _run_model(model_name, bond_data, curve_json, args, instrument_id, bond_file.name)


def run_all_bonds(curve_json, args):
    collected = []
    for bond_file in sorted(ASSETS_DIR.glob('*.json')):
        if bond_file.name.startswith('.'):
            continue
        try:
            result_entry = dispatch_one(bond_file, curve_json, args)
            if result_entry is not None:
                collected.append(result_entry)
        except Exception as exc:
            print(f'{bond_file.name}')
            print(f'Skipped: {exc}')
            print()
    if collected:
        out_path = json_report.create_json_report(collected)
        print(f'JSON summary: {out_path}')


def _normalize_dates(bond_data: dict) -> dict:
    data = dict(bond_data)

    def to_ddmmyyyy(val):
        if not isinstance(val, str):
            return val
        s = val.strip()
        if len(s) == 10 and s[2] == '-' and s[5] == '-':
            return s
        if len(s) == 10 and s[4] == '-' and s[7] == '-':
            year, month, day = s.split('-')
            return f'{day}-{month}-{year}'
        return s

    for field in ['evaluation_date', 'issue_date', 'maturity_date',
                  'first_coupon_date', 'interest_commencement_date',
                  'expiry_date', 'trade_date']:
        if field in data:
            data[field] = to_ddmmyyyy(data[field])

    if 'call_dates' in data and isinstance(data['call_dates'], list):
        data['call_dates'] = [to_ddmmyyyy(d) for d in data['call_dates']]

    return data


def main():
    args = parse_args()
    validate_asset_filenames_by_isin()
    curve_file = resolve_curve_path(args.curve_file)
    curve_json = hullwhite.load_json(curve_file)

    bond_selector = args.bond if args.bond is not None else args.bond_file
    run_all_requested = args.all_bonds or str(bond_selector).strip().lower() == 'all'

    if run_all_requested:
        update_swap_curves_ecb(verbose=True)
        update_swap_curves_fed(verbose=True)
        run_all_bonds(curve_json, args)
        return

    bond_file = resolve_asset_path(str(bond_selector))
    dispatch_one(bond_file, curve_json, args)


if __name__ == '__main__':
    main()
