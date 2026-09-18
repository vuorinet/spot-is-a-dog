from __future__ import annotations

from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, patch

import pytest

from spot import elering, entsoe, nordpool, price_source
from spot.entsoe import DataNotAvailable, DaySeries, PricePoint

_TARGET_DATE = date(2026, 8, 31)


def _point(hour: int, price: float) -> PricePoint:
    start = datetime(2026, 8, 30, 21, 0, tzinfo=UTC).replace(hour=hour % 24)
    return PricePoint(
        start_utc=start,
        end_utc=start.replace(hour=(hour + 1) % 24),
        price_eur_per_mwh=price,
    )


def _series(*points: PricePoint) -> DaySeries:
    return DaySeries(
        market="FI",
        granularity="quarter_hour",
        points=list(points),
        published_at_utc=None,
    )


@pytest.mark.asyncio
async def test_uses_entsoe_data_when_all_sources_agree():
    p = _point(21, 1.0)
    with (
        patch.object(
            entsoe,
            "fetch_day_ahead_prices",
            new=AsyncMock(return_value=_series(p)),
        ),
        patch.object(
            elering,
            "fetch_day_ahead_prices",
            new=AsyncMock(return_value=_series(_point(21, 999.0))),
        ),
        patch.object(
            nordpool,
            "fetch_day_ahead_prices",
            new=AsyncMock(return_value=_series(_point(21, 999.0))),
        ),
    ):
        series = await price_source.fetch_day_ahead_prices("token", _TARGET_DATE)

    # ENTSO-E is queried alongside the fallbacks (not only when it fails),
    # and wins any slot it has data for.
    assert series.points == [p]


@pytest.mark.asyncio
async def test_all_sources_are_always_queried():
    with (
        patch.object(
            entsoe,
            "fetch_day_ahead_prices",
            new=AsyncMock(return_value=_series(_point(21, 1.0))),
        ) as mock_entsoe,
        patch.object(
            elering,
            "fetch_day_ahead_prices",
            new=AsyncMock(return_value=_series(_point(22, 2.0))),
        ) as mock_elering,
        patch.object(
            nordpool,
            "fetch_day_ahead_prices",
            new=AsyncMock(return_value=_series(_point(23, 3.0))),
        ) as mock_nordpool,
    ):
        await price_source.fetch_day_ahead_prices("token", _TARGET_DATE)

    mock_entsoe.assert_called_once()
    mock_elering.assert_called_once()
    mock_nordpool.assert_called_once()


@pytest.mark.asyncio
async def test_fills_gaps_left_by_entsoe_from_elering():
    """The core scenario this merge exists for: ENTSO-E only has part of

    the day (e.g. the auction isn't fully published yet), but Elering
    already has the missing hours - the merged result should have both.
    """
    entsoe_series = _series(_point(21, 1.0))
    elering_series = _series(_point(21, 999.0), _point(22, 2.0), _point(23, 3.0))
    with (
        patch.object(
            entsoe,
            "fetch_day_ahead_prices",
            new=AsyncMock(return_value=entsoe_series),
        ),
        patch.object(
            elering,
            "fetch_day_ahead_prices",
            new=AsyncMock(return_value=elering_series),
        ),
        patch.object(
            nordpool,
            "fetch_day_ahead_prices",
            new=AsyncMock(side_effect=DataNotAvailable("not yet")),
        ),
    ):
        series = await price_source.fetch_day_ahead_prices("token", _TARGET_DATE)

    assert sorted(series.points, key=lambda p: p.start_utc) == [
        _point(21, 1.0),  # from ENTSO-E, wins over Elering's 999.0 for the same slot
        _point(22, 2.0),  # filled from Elering
        _point(23, 3.0),  # filled from Elering
    ]


@pytest.mark.asyncio
async def test_falls_back_to_nordpool_when_entsoe_and_elering_fail():
    p = _point(21, 3.0)
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
            new=AsyncMock(return_value=_series(p)),
        ),
    ):
        series = await price_source.fetch_day_ahead_prices("token", _TARGET_DATE)

    assert series.points == [p]


@pytest.mark.asyncio
async def test_one_source_raising_unexpectedly_does_not_crash_the_others():
    """Sources are queried concurrently via asyncio.gather(return_exceptions=True):

    an unexpected crash in one coroutine (not just the HTTP/DataNotAvailable
    errors the sources normally raise) must not stop the other two from
    running, and their data must still be used.
    """
    p = _point(21, 5.0)
    with (
        patch.object(
            entsoe,
            "fetch_day_ahead_prices",
            new=AsyncMock(side_effect=TypeError("boom - unexpected bug in ENTSO-E parsing")),
        ),
        patch.object(
            elering,
            "fetch_day_ahead_prices",
            new=AsyncMock(return_value=_series(p)),
        ),
        patch.object(
            nordpool,
            "fetch_day_ahead_prices",
            new=AsyncMock(side_effect=ConnectionError("network boom")),
        ),
    ):
        series = await price_source.fetch_day_ahead_prices("token", _TARGET_DATE)

    assert series.points == [p]


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
async def test_logs_individual_failures_as_warnings_and_total_failure_as_error(caplog):
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
        caplog.at_level("WARNING", logger="spot.price_source"),
        pytest.raises(price_source.AllPriceSourcesFailedError),
    ):
        await price_source.fetch_day_ahead_prices("token", _TARGET_DATE)

    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    errors = [r for r in caplog.records if r.levelname == "ERROR"]

    assert len(warnings) == 3  # one per failed source
    assert any("entsoe down" in r.message for r in warnings)
    assert any("elering down" in r.message for r in warnings)
    assert any("nordpool down" in r.message for r in warnings)

    assert len(errors) == 1  # summary once every source has failed
    assert "All price sources failed" in errors[0].message


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
async def test_uses_fallback_data_even_when_entsoe_says_not_available_yet():
    """Publication timing can differ slightly between vendors, so a

    DataNotAvailable from ENTSO-E should not prevent using Elering/Nord
    Pool data that's already published.
    """
    p = _point(21, 4.0)
    with (
        patch.object(
            entsoe,
            "fetch_day_ahead_prices",
            new=AsyncMock(side_effect=DataNotAvailable("not yet")),
        ),
        patch.object(
            elering,
            "fetch_day_ahead_prices",
            new=AsyncMock(return_value=_series(p)),
        ),
        patch.object(
            nordpool,
            "fetch_day_ahead_prices",
            new=AsyncMock(return_value=_series(p)),
        ),
    ):
        series = await price_source.fetch_day_ahead_prices("token", _TARGET_DATE)

    assert series.points == [p]
