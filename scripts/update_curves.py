#!/usr/bin/env python3

"""Convenience wrapper to update swap curves from ECB, Fed, and CDS data."""

import argparse
import importlib.util
import sys
from pathlib import Path

# Support both direct execution (python scripts/update_curves.py) and
# package import (from .update_curves import ...).  Relative imports fail
# in the former case, so we load sibling modules by file path instead.
_SCRIPTS_DIR = Path(__file__).resolve().parent

def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS_DIR / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

update_swap_curves_ecb = _load("update_ecb").update_swap_curves_ecb
update_swap_curves_fed = _load("update_fed").update_swap_curves_fed
update_swap_curves_cds = _load("update_cds").update_swap_curves_cds


def parse_args():
    parser = argparse.ArgumentParser(
        description="Update curves/swap_curves.json from ECB SDW, FRED, and investing.com CDS."
    )
    parser.add_argument(
        "--curve-file",
        default=None,
        help="Optional path to swap_curves.json (defaults to curves/swap_curves.json).",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress progress output.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    curve_file = Path(args.curve_file) if args.curve_file else None
    verbose = not args.quiet

    try:
        update_swap_curves_ecb(curve_file, verbose=verbose)
    except Exception as e:
        print(f"Error running ECB updater: {e}")

    try:
        update_swap_curves_fed(curve_file, verbose=verbose)
    except Exception as e:
        print(f"Error running Fed updater: {e}")

    try:
        update_swap_curves_cds(curve_file, verbose=verbose)
    except Exception as e:
        print(f"Error running CDS updater: {e}")


if __name__ == "__main__":
    main()
