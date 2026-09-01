from __future__ import annotations

from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from spot import elering
from spot.entsoe import DataNotAvailable


def _mock_response(status_code: int, json_body: dict | None = None) -> httpx.Response:
    return httpx.Response(
        status_code,
        json=json_body,
        request=httpx.Request("GET", elering.API_URL),
    )


@pytest.mark.asyncio
async def test_fetch_day_ahead_prices_returns_quarter_hour_series():
    # 2026-08-31T00:00 Helsinki local (EEST, UTC+3) == 2026-08-30T21:00Z
    first_start = datetime(2026, 8, 30, 21, 0, tzinfo=UTC)
    second_start = datetime(2026, 8, 30, 21, 15, tzinfo=UTC)
    payload = {
        "success": True,
        "data": {
            "fi": [
                {"timestamp": int(first_start.timestamp()), "price": 27.55},
                {"timestamp": int(second_start.timestamp()), "price": 27.89},
            ],
        },
    }
    with patch.object(
        httpx.AsyncClient,
        "get",
        new=AsyncMock(return_value=_mock_response(200, payload)),
    ):
        series = await elering.fetch_day_ahead_prices(date(2026, 8, 31))

    assert series.granularity == "quarter_hour"
    assert len(series.points) == 2
    assert series.points[0].start_utc == first_start
    assert series.points[0].price_eur_per_mwh == 27.55
    assert series.points[1].start_utc == second_start


@pytest.mark.asyncio
async def test_raises_elering_api_error_on_http_error():
    with (
        patch.object(
            httpx.AsyncClient,
            "get",
            new=AsyncMock(return_value=_mock_response(503)),
        ),
        pytest.raises(elering.EleringAPIError),
    ):
        await elering.fetch_day_ahead_prices(date(2026, 8, 31))


@pytest.mark.asyncio
async def test_raises_data_not_available_when_no_points_in_window():
    payload = {"success": True, "data": {"fi": []}}
    with (
        patch.object(
            httpx.AsyncClient,
            "get",
            new=AsyncMock(return_value=_mock_response(200, payload)),
        ),
        pytest.raises(DataNotAvailable),
    ):
        await elering.fetch_day_ahead_prices(date(2026, 8, 31))


@pytest.mark.asyncio
async def test_raises_elering_api_error_when_success_false():
    payload = {"success": False}
    with (
        patch.object(
            httpx.AsyncClient,
            "get",
            new=AsyncMock(return_value=_mock_response(200, payload)),
        ),
        pytest.raises(elering.EleringAPIError),
    ):
        await elering.fetch_day_ahead_prices(date(2026, 8, 31))
