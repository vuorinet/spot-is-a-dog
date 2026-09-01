from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta

import httpx
from dateutil import tz

from .entsoe import DataNotAvailable, DaySeries, PricePoint

API_URL = "https://dashboard.elering.ee/api/nps/price"
REGION_FINLAND = "fi"
HELSINKI_TZ = tz.gettz("Europe/Helsinki")
logger = logging.getLogger("spot.elering")


class EleringAPIError(RuntimeError): ...


async def fetch_day_ahead_prices(target_date: date) -> DaySeries:
    """Fetch Finland day-ahead prices for target_date (Helsinki local day).

    Fallback source for ENTSO-E: Elering (Estonia's TSO) re-publishes Nord
    Pool day-ahead prices, natively at 15-minute resolution, for Finland
    under the "fi" field. No auth required.
    """
    period_start_local = datetime(
        target_date.year,
        target_date.month,
        target_date.day,
        0,
        0,
        tzinfo=HELSINKI_TZ,
    )
    period_end_local = period_start_local + timedelta(days=1)
    period_start = period_start_local.astimezone(UTC)
    period_end = period_end_local.astimezone(UTC)

    params = {
        "start": period_start.isoformat(),
        "end": period_end.isoformat(),
        "fields": REGION_FINLAND,
    }

    async with httpx.AsyncClient(timeout=30) as client:
        try:
            r = await client.get(API_URL, params=params)
            r.raise_for_status()
        except httpx.HTTPError as exc:
            msg = f"Error while downloading Elering data: {exc}"
            raise EleringAPIError(msg) from exc
        payload = r.json()

    if not payload.get("success"):
        msg = f"Elering API reported failure: {payload}"
        raise EleringAPIError(msg)

    try:
        entries = payload["data"][REGION_FINLAND]
    except KeyError as exc:
        msg = f"Elering response missing '{REGION_FINLAND}' price data"
        raise EleringAPIError(msg) from exc

    points: list[PricePoint] = []
    for entry in entries:
        start = datetime.fromtimestamp(entry["timestamp"], tz=UTC)
        if not (period_start <= start < period_end):
            continue
        points.append(
            PricePoint(
                start_utc=start,
                end_utc=start + timedelta(minutes=15),
                price_eur_per_mwh=float(entry["price"]),
            ),
        )

    if not points:
        msg = f"No Elering price data for {target_date}"
        raise DataNotAvailable(msg)

    points.sort(key=lambda p: p.start_utc)
    return DaySeries(
        market="FI",
        granularity="quarter_hour",
        points=points,
        published_at_utc=None,
    )
