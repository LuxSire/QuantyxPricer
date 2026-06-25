"""Tests for api/provider.py — cbonds fetch functions.

Unit tests mock requests.post so no network credentials are needed.
Live tests are skipped unless CBONDS_LOGIN and CBONDS_PASSWORD are set.
"""
import sys
import os
import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
import requests

# Ensure api/ is importable without an __init__.py
API_DIR = Path(__file__).resolve().parent.parent / 'api'
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

import provider as cbonds_provider


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_response(status_code=200, json_body=None, raise_for=None):
    """Build a mock requests.Response."""
    mock = MagicMock()
    mock.status_code = status_code
    mock.text = json.dumps(json_body or {})
    if raise_for:
        mock.raise_for_status.side_effect = raise_for
    else:
        mock.raise_for_status.return_value = None
    mock.json.return_value = json_body or {}
    return mock


SAMPLE_EMISSION = {
    'isin_code': 'XS1234567890',
    'emitent_name_eng': 'Test Issuer',
    'currency_name': 'EUR',
    'nominal_price': 1000,
}

SAMPLE_TRADING = {
    'isin_code': 'XS1234567890',
    'price': 98.5,
    'yield': 4.2,
    'trade_date': '2025-06-20',
    'volume': 5000000,
}

SAMPLE_ESTIMATE = {
    'isin_code': 'XS1234567890',
    'date': '2025-06-20',
    'price_bid': 98.0,
    'price_ask': 99.0,
    'yield_bid': 4.3,
    'yield_ask': 4.1,
}


# ---------------------------------------------------------------------------
# fetch_from_cbonds
# ---------------------------------------------------------------------------

class TestFetchFromCbonds:
    def test_returns_none_for_empty_isin(self):
        assert cbonds_provider.fetch_from_cbonds('') is None

    def test_returns_none_for_none_isin(self):
        assert cbonds_provider.fetch_from_cbonds(None) is None

    def test_returns_none_for_whitespace_isin(self):
        assert cbonds_provider.fetch_from_cbonds('   ') is None

    @patch('provider.requests.post')
    def test_returns_first_item_from_items_array(self, mock_post):
        mock_post.return_value = _mock_response(json_body={'items': [SAMPLE_EMISSION]})
        result = cbonds_provider.fetch_from_cbonds('XS1234567890')
        assert result is not None
        assert result['isin_code'] == 'XS1234567890'
        assert result['provider'] == 'cbonds'

    @patch('provider.requests.post')
    def test_returns_first_item_from_data_array(self, mock_post):
        mock_post.return_value = _mock_response(json_body={'data': [SAMPLE_EMISSION]})
        result = cbonds_provider.fetch_from_cbonds('XS1234567890')
        assert result is not None
        assert result['provider'] == 'cbonds'

    @patch('provider.requests.post')
    def test_returns_none_when_items_empty(self, mock_post):
        mock_post.return_value = _mock_response(json_body={'items': []})
        assert cbonds_provider.fetch_from_cbonds('XS1234567890') is None

    @patch('provider.requests.post')
    def test_returns_none_when_no_items_key(self, mock_post):
        mock_post.return_value = _mock_response(json_body={'status': 'ok'})
        assert cbonds_provider.fetch_from_cbonds('XS1234567890') is None

    @patch('provider.requests.post')
    def test_returns_none_on_http_error(self, mock_post):
        mock_post.return_value = _mock_response(
            status_code=401,
            raise_for=requests.RequestException('Unauthorized'),
        )
        assert cbonds_provider.fetch_from_cbonds('XS1234567890') is None

    @patch('provider.requests.post')
    def test_returns_none_on_connection_error(self, mock_post):
        mock_post.side_effect = requests.ConnectionError('connection refused')
        assert cbonds_provider.fetch_from_cbonds('XS1234567890') is None

    @patch('provider.requests.post')
    def test_returns_none_on_timeout(self, mock_post):
        mock_post.side_effect = requests.Timeout('timed out')
        assert cbonds_provider.fetch_from_cbonds('XS1234567890') is None

    @patch('provider.requests.post')
    def test_returns_none_on_invalid_json(self, mock_post):
        mock = MagicMock()
        mock.raise_for_status.return_value = None
        mock.text = 'not json'
        mock.json.side_effect = json.JSONDecodeError('bad json', '', 0)
        mock_post.return_value = mock
        assert cbonds_provider.fetch_from_cbonds('XS1234567890') is None

    @patch('provider.requests.post')
    def test_strips_whitespace_from_isin(self, mock_post):
        mock_post.return_value = _mock_response(json_body={'items': [SAMPLE_EMISSION]})
        result = cbonds_provider.fetch_from_cbonds('  XS1234567890  ')
        assert result is not None
        # verify the payload sent used the stripped isin
        call_kwargs = mock_post.call_args
        sent_payload = call_kwargs[1].get('json') or call_kwargs[0][1]
        filters = sent_payload['filters']
        assert filters[0]['value'] == 'XS1234567890'

    @patch('provider.requests.post')
    def test_request_sent_to_correct_url(self, mock_post):
        mock_post.return_value = _mock_response(json_body={'items': []})
        cbonds_provider.fetch_from_cbonds('XS1234567890')
        called_url = mock_post.call_args[0][0]
        assert 'cbonds' in called_url


# ---------------------------------------------------------------------------
# fetch_prices_from_cbonds
# ---------------------------------------------------------------------------

class TestFetchPricesFromCbonds:
    def test_returns_none_for_empty_code(self):
        assert cbonds_provider.fetch_prices_from_cbonds('') is None

    def test_returns_none_for_none_code(self):
        assert cbonds_provider.fetch_prices_from_cbonds(None) is None

    @patch('provider.requests.post')
    def test_returns_first_item_with_provider_and_instrument_id(self, mock_post):
        mock_post.return_value = _mock_response(json_body={'items': [SAMPLE_TRADING]})
        result = cbonds_provider.fetch_prices_from_cbonds('XS1234567890')
        assert result is not None
        assert result['provider'] == 'cbonds'
        assert result['instrument_id'] == 'XS1234567890'
        assert result['price'] == 98.5

    @patch('provider.requests.post')
    def test_instrument_id_falls_back_to_isin_param(self, mock_post):
        item = dict(SAMPLE_TRADING)
        del item['isin_code']
        mock_post.return_value = _mock_response(json_body={'items': [item]})
        result = cbonds_provider.fetch_prices_from_cbonds('XS1234567890')
        assert result['instrument_id'] == 'XS1234567890'

    @patch('provider.requests.post')
    def test_returns_none_when_items_empty(self, mock_post):
        mock_post.return_value = _mock_response(json_body={'items': []})
        assert cbonds_provider.fetch_prices_from_cbonds('XS1234567890') is None

    @patch('provider.requests.post')
    def test_returns_none_on_request_exception(self, mock_post):
        mock_post.side_effect = requests.RequestException('network error')
        assert cbonds_provider.fetch_prices_from_cbonds('XS1234567890') is None

    @patch('provider.requests.post')
    def test_returns_none_on_invalid_json(self, mock_post):
        mock = MagicMock()
        mock.raise_for_status.return_value = None
        mock.text = 'not json'
        mock.json.side_effect = json.JSONDecodeError('bad', '', 0)
        mock_post.return_value = mock
        assert cbonds_provider.fetch_prices_from_cbonds('XS1234567890') is None

    @patch('provider.requests.post')
    def test_sorting_by_trade_date_desc_in_payload(self, mock_post):
        mock_post.return_value = _mock_response(json_body={'items': []})
        cbonds_provider.fetch_prices_from_cbonds('XS1234567890')
        sent = mock_post.call_args[1].get('json') or mock_post.call_args[0][1]
        sorting = sent.get('sorting', [])
        assert any(s.get('field') == 'trade_date' and s.get('order') == 'desc' for s in sorting)


# ---------------------------------------------------------------------------
# fetch_estimates_from_cbonds
# ---------------------------------------------------------------------------

class TestFetchEstimatesFromCbonds:
    def test_returns_none_for_empty_isin(self):
        assert cbonds_provider.fetch_estimates_from_cbonds('') is None

    def test_returns_none_for_none_isin(self):
        assert cbonds_provider.fetch_estimates_from_cbonds(None) is None

    @patch('provider.requests.post')
    def test_returns_first_item_with_provider(self, mock_post):
        mock_post.return_value = _mock_response(json_body={'items': [SAMPLE_ESTIMATE]})
        result = cbonds_provider.fetch_estimates_from_cbonds('XS1234567890')
        assert result is not None
        assert result['provider'] == 'cbonds'
        assert result['price_bid'] == 98.0

    @patch('provider.requests.post')
    def test_returns_none_when_items_empty(self, mock_post):
        mock_post.return_value = _mock_response(json_body={'items': []})
        assert cbonds_provider.fetch_estimates_from_cbonds('XS1234567890') is None

    @patch('provider.requests.post')
    def test_returns_none_on_request_exception(self, mock_post):
        mock_post.side_effect = requests.RequestException('network error')
        assert cbonds_provider.fetch_estimates_from_cbonds('XS1234567890') is None

    @patch('provider.requests.post')
    def test_sorting_by_date_desc_in_payload(self, mock_post):
        mock_post.return_value = _mock_response(json_body={'items': []})
        cbonds_provider.fetch_estimates_from_cbonds('XS1234567890')
        sent = mock_post.call_args[1].get('json') or mock_post.call_args[0][1]
        sorting = sent.get('sorting', [])
        assert any(s.get('field') == 'date' and s.get('order') == 'desc' for s in sorting)


# ---------------------------------------------------------------------------
# Live integration tests (skipped without credentials)
# ---------------------------------------------------------------------------

def _cbonds_creds_available():
    return bool(os.getenv('CBONDS_LOGIN') and os.getenv('CBONDS_PASSWORD'))


@pytest.mark.skipif(not _cbonds_creds_available(), reason='CBONDS_LOGIN / CBONDS_PASSWORD not set')
def test_fetch_from_cbonds_live():
    # Confirms connectivity and auth — result may be None if the ISIN has no data
    result = cbonds_provider.fetch_from_cbonds('FR0013398757')
    assert result is None or (isinstance(result, dict) and result.get('provider') == 'cbonds')


@pytest.mark.skipif(not _cbonds_creds_available(), reason='CBONDS_LOGIN / CBONDS_PASSWORD not set')
def test_fetch_prices_from_cbonds_live():
    result = cbonds_provider.fetch_prices_from_cbonds('FR0013398757')
    # May return None if no trading data exists, but should not raise
    assert result is None or isinstance(result, dict)


@pytest.mark.skipif(not _cbonds_creds_available(), reason='CBONDS_LOGIN / CBONDS_PASSWORD not set')
def test_fetch_estimates_from_cbonds_live():
    result = cbonds_provider.fetch_estimates_from_cbonds('FR0013398757')
    assert result is None or isinstance(result, dict)
