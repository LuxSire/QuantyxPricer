import pytest
import pytest_asyncio
import httpx
import logging

@pytest_asyncio.fixture
async def async_client():
    async with httpx.AsyncClient(base_url="http://127.0.0.1:8000") as client:
        yield client


@pytest.mark.asyncio
async def test_price_endpoint(async_client):
    payload = {
        "instrument_id": "XS0316010023"
    }


    try:
        # Make a POST request to the /price endpoint
        response = await async_client.post("/price", json=payload)
        
    except httpx.HTTPError as e:
        # Log the error details
        logging.error("HTTP error occurred: %s", e)
        raise e

    print(f"[TEST] /price response status: {response.status_code}")
    print(f"[TEST] /price response body: {response.text}")
    assert response.status_code == 200
    # Replace this with the real expected payload from your API
    # assert response.json() == {...}
    
@pytest.mark.asyncio
async def test_download_price_endpoint(async_client):
    payload = {
        "instrument_id": "AAPL"
    }


    try:
        # Make a GET request to the /download_prices endpoint
        response = await async_client.get("/download_prices", params=payload)
    except httpx.HTTPError as e:
        # Log the error details
        logging.error("HTTP error occurred: %s", e)
        raise e

    assert response.status_code == 200
    # Replace this with the real expected payload from your API
    # assert response.json() == {...}