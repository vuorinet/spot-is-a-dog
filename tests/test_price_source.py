from __future__ import annotations

from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, patch

import pytest

from spot import elering, entsoe, nordpool, price_source
from spot.entsoe import DataNotAvailable, DaySeries, PricePoint

_TARGET_DATE = date(2026, 8, 31)


def _series(price: float) -> DaySeries:
    return DaySeries(
        market="FI",
        granularity="quarter_hour",
        points=[
            PricePoint(
                start_utc=datetime(2026, 8, 30, 21, 0, tzinfo=UTC),
                end_utc=datetime(2026, 8, 30, 21, 15, tzinfo=UTC),
                price_eur_per_mwh=price,
            ),
        ],
        published_at_utc=None,
    )


@pytest.mark.asyncio
async def test_uses_entsoe_when_it_succeeds():
    with (
        patch.object(
            entsoe,
            "fetch_day_ahead_prices",
            new=AsyncMock(return_value=_series(1.0)),
        ),
        patch.object(elering, "fetch_day_ahead_prices") as mock_elering,
        patch.object(nordpool, "fetch_day_ahead_prices") as mock_nordpool,
    ):
        series = await price_source.fetch_day_ahead_prices("token", _TARGET_DATE)

    assert series == _series(1.0)
    mock_elering.assert_not_called()
    mock_nordpool.assert_not_called()


@pytest.mark.asyncio
async def test_falls_back_to_elering_when_entsoe_fails():
    with (
        patch.object(
            entsoe,
            "fetch_day_ahead_prices",
            new=AsyncMock(side_effect=RuntimeError("entsoe down")),
        ),
        patch.object(
            elering,
            "fetch_day_ahead_prices",
            new=AsyncMock(return_value=_series(2.0)),
        ),
        patch.object(nordpool, "fetch_day_ahead_prices") as mock_nordpool,
    ):
        series = await price_source.fetch_day_ahead_prices("token", _TARGET_DATE)

    assert series == _series(2.0)
    mock_nordpool.assert_not_called()


@pytest.mark.asyncio
async def test_falls_back_to_nordpool_when_entsoe_and_elering_fail():
    with (
        patch.object(
            entsoe,
            "fetch_day_ahead_prices",
            new=AsyncMock(side_effect=RuntimeError("entsoe down")),
        ),
        patch.object(
            elering,
            "fetch_day_ahead_prices",
            new=AsyncMock(side_effect=elering.EleringAPIError("elering down")),
        ),
        patch.object(
            nordpool,
            "fetch_day_ahead_prices",
            new=AsyncMock(return_value=_series(3.0)),
        ),
    ):
        series = await price_source.fetch_day_ahead_prices("token", _TARGET_DATE)

    assert series == _series(3.0)


@pytest.mark.asyncio
async def test_raises_all_failed_when_every_source_hard_fails():
    with (
        patch.object(
            entsoe,
            "fetch_day_ahead_prices",
            new=AsyncMock(side_effect=RuntimeError("entsoe down")),
        ),
        patch.object(
            elering,
            "fetch_day_ahead_prices",
            new=AsyncMock(side_effect=elering.EleringAPIError("elering down")),
        ),
        patch.object(
            nordpool,
            "fetch_day_ahead_prices",
            new=AsyncMock(side_effect=nordpool.NordPoolAPIError("nordpool down")),
        ),
        pytest.raises(price_source.AllPriceSourcesFailedError),
    ):
        await price_source.fetch_day_ahead_prices("token", _TARGET_DATE)


@pytest.mark.asyncio
async def test_raises_data_not_available_when_no_source_has_published_yet():
    """All sources agreeing "not published yet" must not look like an outage.

    Distinct from test_raises_all_failed_when_every_source_hard_fails: here
    every source explicitly says the day isn't published, which callers
    (main.py) treat as the normal "try again later" case, not an error.
    """
    with (
        patch.object(
            entsoe,
            "fetch_day_ahead_prices",
            new=AsyncMock(side_effect=DataNotAvailable("not yet")),
        ),
        patch.object(
            elering,
            "fetch_day_ahead_prices",
            new=AsyncMock(side_effect=DataNotAvailable("not yet")),
        ),
        patch.object(
            nordpool,
            "fetch_day_ahead_prices",
            new=AsyncMock(side_effect=DataNotAvailable("not yet")),
        ),
        pytest.raises(DataNotAvailable),
    ):
        await price_source.fetch_day_ahead_prices("token", _TARGET_DATE)


@pytest.mark.asyncio
async def test_tries_fallback_even_after_entsoe_says_not_available_yet():
    """Publication timing can differ slightly between vendors, so a

    DataNotAvailable from ENTSO-E should not skip trying the fallbacks.
    """
    with (
        patch.object(
            entsoe,
            "fetch_day_ahead_prices",
            new=AsyncMock(side_effect=DataNotAvailable("not yet")),
        ),
        patch.object(
            elering,
            "fetch_day_ahead_prices",
            new=AsyncMock(return_value=_series(4.0)),
        ),
        patch.object(nordpool, "fetch_day_ahead_prices") as mock_nordpool,
    ):
        series = await price_source.fetch_day_ahead_prices("token", _TARGET_DATE)

    assert series == _series(4.0)
    mock_nordpool.assert_not_called()
