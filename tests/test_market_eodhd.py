from pathlib import Path
import os
import pytest

from models import market

try:
    import pandas as pd
except Exception:
    pd = None


def test_eodhd_bond_fundamentals_live():
    project_root = Path(__file__).resolve().parent.parent
    # load .env if present
    try:
        market._load_env(project_root)
    except Exception:
        pass

    if not os.getenv("EODHD_API_KEY"):
        pytest.skip("EODHD_API_KEY not set; skipping live eodhd test")

    res = market.eodhd_bond_fundamentals("US36166NAJ28", save=False)
    assert res is not None

    if pd is not None and isinstance(res, pd.DataFrame):
        assert not res.empty
    else:
        assert res
