from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from spot import nordpool
from spot.entsoe import DataNotAvailable


def _response(status_code: int, json_body: dict | None = None) -> httpx.Response:
    return httpx.Response(
        status_code,
        json=json_body,
        request=httpx.Request("GET", nordpool.API_URL),
    )


def _entry(start: datetime, price: float) -> dict:
    end = start + timedelta(minutes=15)
    return {
        "deliveryStart": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "deliveryEnd": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "entryPerArea": {"FI": price},
    }


@pytest.mark.asyncio
async def test_fetch_day_ahead_prices_stitches_across_cest_day_boundary():
    # Helsinki-local 2026-08-31 00:00-24:00 == 2026-08-30T21:00Z - 2026-08-31T21:00Z.
    # Nord Pool's own "date" param is CEST-anchored, so the window straddles
    # two of its calendar-day responses.
    in_window_1 = datetime(2026, 8, 30, 21, 0, tzinfo=UTC)
    in_window_2 = datetime(2026, 8, 31, 20, 45, tzinfo=UTC)
    out_of_window = datetime(2026, 8, 31, 21, 0, tzinfo=UTC)  # belongs to next day

    async def fake_get(_self, url, params=None, **_kwargs):  # noqa: ARG001
        query_date = params["date"]
        if query_date == "2026-08-30":
            return _response(200, {"multiAreaEntries": [_entry(in_window_1, 27.55)]})
        if query_date == "2026-08-31":
            return _response(
                200,
                {
                    "multiAreaEntries": [
                        _entry(in_window_2, 40.3),
                        _entry(out_of_window, 39.91),
                    ],
                },
            )
        return _response(204)  # 2026-09-01: not yet published

    with patch.object(httpx.AsyncClient, "get", new=fake_get):
        series = await nordpool.fetch_day_ahead_prices(date(2026, 8, 31))

    assert series.granularity == "quarter_hour"
    starts = [p.start_utc for p in series.points]
    assert starts == [in_window_1, in_window_2]
    assert out_of_window not in starts


@pytest.mark.asyncio
async def test_raises_nordpool_api_error_on_http_error():
    with (
        patch.object(
            httpx.AsyncClient,
            "get",
            new=AsyncMock(return_value=_response(503)),
        ),
        pytest.raises(nordpool.NordPoolAPIError),
    ):
        await nordpool.fetch_day_ahead_prices(date(2026, 8, 31))


@pytest.mark.asyncio
async def test_treats_204_as_no_data_not_an_error():
    with (
        patch.object(
            httpx.AsyncClient,
            "get",
            new=AsyncMock(return_value=_response(204)),
        ),
        pytest.raises(DataNotAvailable),
    ):
        await nordpool.fetch_day_ahead_prices(date(2026, 8, 31))
