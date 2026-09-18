from __future__ import annotations

import asyncio
import logging
import typing as t
from datetime import date, datetime

from . import elering, entsoe, nordpool
from .entsoe import DataNotAvailable, DaySeries, PricePoint

logger = logging.getLogger("spot.price_source")


class AllPriceSourcesFailedError(RuntimeError): ...


async def fetch_day_ahead_prices(
    token: str,
    target_date: date,
    prefer_15min: bool = False,
) -> DaySeries:
    """Fetch day-ahead prices, merging every source to fill in gaps.

    Queries ENTSO-E (the primary, official EU transparency platform),
    Elering (Estonia's TSO, re-publishes Nord Pool data) and the Nord Pool
    Data Portal directly, concurrently, on every fetch.

    It's normal for any single source to only have part of the day -
    resolution, boundary handling and publication timing all differ
    slightly between vendors, especially before a day is fully published -
    so rather than treating "first source with any data wins" and
    discarding the rest, every source's points are merged by exact
    interval start time. ENTSO-E wins any slot it has data for; Elering
    and then Nord Pool fill in whatever ENTSO-E is missing.

    Raises:
        DataNotAvailable: If no source has published anything for this
            day yet.
        AllPriceSourcesFailedError: If every source hard-failed (as
            opposed to simply not having data yet) and nothing could be
            merged.
    """
    fetches: list[tuple[str, t.Coroutine[t.Any, t.Any, DaySeries]]] = [
        ("ENTSO-E", entsoe.fetch_day_ahead_prices(token, target_date, prefer_15min=prefer_15min)),
        ("Elering", elering.fetch_day_ahead_prices(target_date)),
        ("Nord Pool", nordpool.fetch_day_ahead_prices(target_date)),
    ]
    outcomes = await asyncio.gather(
        *(coro for _, coro in fetches),
        return_exceptions=True,
    )

    series_by_source: list[tuple[str, DaySeries]] = []
    not_yet_available: list[str] = []
    failures: list[str] = []

    for (name, _), outcome in zip(fetches, outcomes, strict=True):
        if isinstance(outcome, DataNotAvailable):
            not_yet_available.append(f"{name}: {outcome}")
        elif isinstance(outcome, BaseException):
            logger.warning("%s price fetch failed for %s: %s", name, target_date, outcome)
            failures.append(f"{name}: {outcome}")
        else:
            series_by_source.append((name, outcome))

    merged = _merge(target_date, series_by_source)
    if merged is not None:
        return merged

    if not failures:
        # Every source agreed the data just isn't published yet - preserve
        # that as the normal, expected signal rather than a hard error.
        raise DataNotAvailable("; ".join(not_yet_available))

    msg = (
        f"All price sources failed for {target_date}: "
        + "; ".join([*failures, *not_yet_available])
    )
    logger.error(msg)
    raise AllPriceSourcesFailedError(msg)


def _merge(
    target_date: date,
    series_by_source: list[tuple[str, DaySeries]],
) -> DaySeries | None:
    """Combine points from multiple sources into one day, keyed by exact
    interval start time.

    `series_by_source` must be in priority order: the first source to
    have a point for a given slot wins it, later sources only fill slots
    none of the earlier ones had.
    """
    if not series_by_source:
        return None

    by_start: dict[datetime, PricePoint] = {}
    contributed: dict[str, int] = {}
    for name, series in series_by_source:
        added = 0
        for p in series.points:
            if p.start_utc not in by_start:
                by_start[p.start_utc] = p
                added += 1
        if added:
            contributed[name] = added

    if not by_start:
        return None

    if len(contributed) > 1:
        logger.warning(
            "Merged %s prices from multiple sources: %s",
            target_date,
            ", ".join(f"{name} ({n})" for name, n in contributed.items()),
        )
    elif next(iter(contributed)) != series_by_source[0][0]:
        logger.warning(
            "Using fallback price source for %s: %s",
            target_date,
            next(iter(contributed)),
        )

    points = sorted(by_start.values(), key=lambda p: p.start_utc)
    granularity = (
        "quarter_hour"
        if any(series.granularity == "quarter_hour" for _, series in series_by_source)
        else series_by_source[0][1].granularity
    )
    published_at_utc = next(
        (series.published_at_utc for _, series in series_by_source if series.published_at_utc),
        None,
    )
    return DaySeries(
        market=series_by_source[0][1].market,
        granularity=granularity,
        points=points,
        published_at_utc=published_at_utc,
    )
