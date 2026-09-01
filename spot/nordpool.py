from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta

import httpx
from dateutil import tz

from .entsoe import DataNotAvailable, DaySeries, PricePoint

API_URL = "https://dataportal-api.nordpoolgroup.com/api/DayAheadPrices"
DELIVERY_AREA_FINLAND = "FI"
HELSINKI_TZ = tz.gettz("Europe/Helsinki")
HTTP_NOT_FOUND = 404
logger = logging.getLogger("spot.nordpool")


class NordPoolAPIError(RuntimeError): ...


async def _download_json_for_date(client: httpx.AsyncClient, query_date: date) -> dict:
    params = {
        "date": query_date.isoformat(),
        "market": "DayAhead",
        "deliveryArea": DELIVERY_AREA_FINLAND,
        "currency": "EUR",
    }
    try:
        r = await client.get(
            API_URL,
            params=params,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        if r.status_code == HTTP_NOT_FOUND:
            return {}
        r.raise_for_status()
    except httpx.HTTPError as exc:
        msg = f"Error while downloading Nord Pool data: {exc}"
        raise NordPoolAPIError(msg) from exc
    if not r.content:
        return {}  # No data published yet for this day.
    return r.json()


async def fetch_day_ahead_prices(target_date: date) -> DaySeries:
    """Fetch Finland day-ahead prices for target_date (Helsinki local day).

    Fallback source for ENTSO-E: queries the Nord Pool exchange's own data
    portal directly (the endpoint data.nordpoolgroup.com's frontend uses),
    natively at 15-minute resolution, for the FI delivery area. No auth
    required.

    Nord Pool's "date" param is a CET/CEST calendar day, not a UTC or
    Helsinki-local day, so it can be offset from our target window by up to
    an hour or two — days on both sides are queried and filtered down to
    the exact Helsinki-local [00:00, 24:00) UTC window.
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

    points: list[PricePoint] = []
    async with httpx.AsyncClient(timeout=30) as client:
        for offset in (-1, 0, 1):
            payload = await _download_json_for_date(
                client,
                target_date + timedelta(days=offset),
            )
            for entry in payload.get("multiAreaEntries") or []:
                try:
                    price = entry["entryPerArea"][DELIVERY_AREA_FINLAND]
                except KeyError:
                    continue
                start = datetime.strptime(
                    entry["deliveryStart"],
                    "%Y-%m-%dT%H:%M:%SZ",
                ).replace(tzinfo=UTC)
                if not (period_start <= start < period_end):
                    continue
                points.append(
                    PricePoint(
                        start_utc=start,
                        end_utc=start + timedelta(minutes=15),
                        price_eur_per_mwh=float(price),
                    ),
                )

    if not points:
        msg = f"No Nord Pool price data for {target_date}"
        raise DataNotAvailable(msg)

    points.sort(key=lambda p: p.start_utc)
    return DaySeries(
        market="FI",
        granularity="quarter_hour",
        points=points,
        published_at_utc=None,
    )
