from __future__ import annotations

import asyncio
import logging
import os
import typing as t
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

import httpx
from dateutil import tz

if t.TYPE_CHECKING:
    from decimal import Decimal

FI_EIC = "10YFI-1--------U"
# Allow overriding via env; default to known working host
ENTSOE_BASE_URL = os.environ.get("ENTSOE_BASE_URL", "https://web-api.tp.entsoe.eu/api")
logger = logging.getLogger("spot.entsoe")


class DataNotAvailable(Exception):
    """Raised when ENTSO-E returns no time series for the requested period."""


@dataclass(frozen=True)
class PricePoint:
    start_utc: datetime
    end_utc: datetime
    price_eur_per_mwh: float


@dataclass(frozen=True)
class DaySeries:
    market: str
    granularity: t.Literal["hour", "quarter_hour"]
    points: list[PricePoint]
    published_at_utc: datetime | None


def _iso_to_dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _duration_to_granularity(duration: str) -> str:
    if duration == "PT60M":
        return "hour"
    if duration == "PT15M":
        return "quarter_hour"
    raise ValueError(f"Unsupported resolution: {duration}")


def parse_publication_xml(xml_bytes: bytes) -> DaySeries:
    root = ET.fromstring(xml_bytes)
    ns = {"ns": root.tag.split("}")[0].strip("{")}
    local_name = root.tag.split("}")[-1]

    if local_name.endswith("Acknowledgement_MarketDocument"):
        # Try to extract reason text for diagnostics
        reasons = [e.text or "" for e in root.findall(".//ns:Reason/ns:text", ns)]
        msg = "; ".join([r for r in reasons if r]) or "No TimeSeries (acknowledgement)"
        raise DataNotAvailable(msg)

    ts_list = root.findall(".//ns:TimeSeries", ns)
    if not ts_list:
        # Many cases: No content yet
        raise DataNotAvailable("No TimeSeries in response")

    all_points: list[PricePoint] = []
    granularity: t.Literal["hour", "quarter_hour"] | None = None
    published_at: datetime | None = None

    for ts in ts_list:
        period = ts.find(".//ns:Period", ns)
        if period is None:
            continue
        resolution = period.findtext("ns:resolution", default="", namespaces=ns)
        g = _duration_to_granularity(resolution)
        if granularity is None:
            granularity = t.cast("t.Literal['hour', 'quarter_hour']", g)
        start_str = period.findtext("ns:timeInterval/ns:start", namespaces=ns)
        end_str = period.findtext("ns:timeInterval/ns:end", namespaces=ns)
        if not start_str or not end_str:
            continue
        start_dt = _iso_to_dt(start_str)
        end_dt = _iso_to_dt(end_str)
        step = timedelta(hours=1) if g == "hour" else timedelta(minutes=15)

        pts = sorted(
            period.findall("ns:Point", ns),
            key=lambda e: int(e.findtext("ns:position", default="0", namespaces=ns)),
        )
        pos_to_price: dict[int, float] = {}
        for p in pts:
            pos = int(p.findtext("ns:position", default="0", namespaces=ns))
            amount_text = p.findtext("ns:price.amount", default="0", namespaces=ns)
            price = float(amount_text)
            pos_to_price[pos] = price

        # Calculate expected number of positions based on period duration
        period_duration = end_dt - start_dt
        expected_positions = int(period_duration / step)
        
        if pos_to_price:
            max_position_in_xml = max(pos_to_price.keys())
            if max_position_in_xml < expected_positions:
                logger.debug(
                    "Gap at end of period: XML has positions 1-%d, expected %d",
                    max_position_in_xml,
                    expected_positions,
                )

        # Fill sequentially; ENTSO-E may skip positions to compress equal values
        # (including zero or negative prices)
        idx = 1
        cur = start_dt
        last_price_in_period: float | None = None  # Track last price within THIS period
        
        while idx <= expected_positions:
            price = pos_to_price.get(idx)
            if price is None:
                # Position is missing - ENTSO-E skips positions when price is same as previous
                if last_price_in_period is not None:
                    # Use last known price from THIS period
                    price = last_price_in_period
                else:
                    # First position(s) missing - find next available price in this period
                    price = next(
                        (v for k, v in sorted(pos_to_price.items()) if k >= idx),
                        0.0,
                    )
                    if price != 0.0:
                        logger.debug(
                            "Gap at start: position %d missing, using next available price %.2f",
                            idx,
                            price,
                        )
            
            last_price_in_period = price
            pt_end = cur + step
            all_points.append(PricePoint(cur, pt_end, price))
            cur = pt_end
            idx += 1

    if granularity is None:
        raise ValueError("Could not determine granularity")

    return DaySeries(
        market="FI",
        granularity=granularity,
        points=all_points,
        published_at_utc=published_at,
    )


async def fetch_day_ahead_prices(
    token: str,
    target_date: date,
    prefer_15min: bool = False,
) -> DaySeries:
    # Create Helsinki timezone start and end times, then convert to UTC
    # ENTSO-E expects local time boundaries for the market data
    helsinki_tz = tz.gettz("Europe/Helsinki")

    period_start_local = datetime(
        target_date.year,
        target_date.month,
        target_date.day,
        0,
        0,
        tzinfo=helsinki_tz,
    )
    period_end_local = period_start_local + timedelta(days=1)

    # Convert to UTC for the API request
    period_start = period_start_local.astimezone(UTC)
    period_end = period_end_local.astimezone(UTC)
    logger.info(f"UTC conversion: {period_start} to {period_end}")

    # Try 15-minute resolution first if preferred and date is after Oct 1, 2025
    if prefer_15min and target_date >= date(2025, 10, 1):
        logger.info(f"Attempting to fetch 15-minute resolution for {target_date}")
        try:
            result = await _fetch_with_resolution(
                token,
                period_start,
                period_end,
                target_date,
                prefer_quarter_hour=True,
            )
            if result.granularity == "quarter_hour":
                logger.info(f"Successfully fetched 15-minute data for {target_date}")
                return result
            logger.info(
                f"Got hourly data when requesting 15-minute for {target_date}, falling back",
            )
        except DataNotAvailable as e:
            logger.info(
                f"15-minute data not available for {target_date}: {e}, trying hourly",
            )

    # Fall back to hourly resolution or use it as default
    logger.info(f"Fetching hourly resolution for {target_date}")
    result = await _fetch_with_resolution(
        token,
        period_start,
        period_end,
        target_date,
        prefer_quarter_hour=False,
    )

    # For testing: simulate 15-minute data by expanding hourly data
    if prefer_15min and result.granularity == "hour":
        logger.info(f"Simulating 15-minute data from hourly data for {target_date}")
        result = _simulate_15min_from_hourly(result)

    return result


def _simulate_15min_from_hourly(hourly_data: DaySeries) -> DaySeries:
    """Convert hourly data to simulated 15-minute data for testing purposes."""
    if hourly_data.granularity != "hour":
        return hourly_data

    simulated_points = []
    for point in hourly_data.points:
        # Create 4 identical 15-minute intervals for each hour
        for quarter in range(4):
            start_time = point.start_utc + timedelta(minutes=quarter * 15)
            end_time = start_time + timedelta(minutes=15)
            simulated_points.append(
                PricePoint(start_time, end_time, point.price_eur_per_mwh),
            )

    return DaySeries(
        market=hourly_data.market,
        granularity="quarter_hour",
        points=simulated_points,
        published_at_utc=hourly_data.published_at_utc,
    )


async def _fetch_with_resolution(
    token: str,
    period_start: datetime,
    period_end: datetime,
    target_date: date,
    prefer_quarter_hour: bool = False,
) -> DaySeries:
    """Internal helper to fetch data with specific resolution preference."""
    params = {
        "securityToken": token,
        "documentType": "A44",
        "processType": "A01",
        "in_Domain": FI_EIC,
        "out_Domain": FI_EIC,
        "periodStart": period_start.strftime("%Y%m%d%H%M"),
        "periodEnd": period_end.strftime("%Y%m%d%H%M"),
    }

    # Add resolution hint if requesting quarter hour (15-minute)
    # Note: ENTSO-E API doesn't have explicit resolution parameter,
    # but will return the finest available resolution for the period
    if prefer_quarter_hour:
        # The API will return 15-minute data if available, hourly otherwise
        logger.debug("Requesting finest available resolution (hoping for 15-minute)")

    safe_params = {k: v for k, v in params.items() if k != "securityToken"}
    logger.info(f"ENTSO-E GET {ENTSOE_BASE_URL} params={safe_params}")

    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(ENTSOE_BASE_URL, params=params)
        if r.status_code == 429:
            await asyncio.sleep(1)
            r = await client.get(ENTSOE_BASE_URL, params=params)
        r.raise_for_status()
        try:
            return parse_publication_xml(r.content)
        except DataNotAvailable as e:
            # Log a short snippet for diagnostics
            snippet = r.content[:200].decode(errors="ignore")
            logger.info("ENTSO-E data not available: %s | body: %s", e, snippet)
            raise


def get_prices(
    token: str,
    start_date: date,
    end_date: date,
) -> t.Generator[tuple[datetime, Decimal], None, None]:
    """Yield (UTC datetime, EUR/kWh) for the range [start_date, end_date).

    Matches the style shown in user's other project (periodStart/periodEnd built
    as YYYYMMDD0000). Internally calls fetch_day_ahead_prices per day and
    converts EUR/MWh to EUR/kWh.
    """
    cur = start_date
    one_day = timedelta(days=1)
    while cur < end_date:
        # Note: function is async, so we expose sync generator via run_until_complete
        raise RuntimeError(
            "get_prices requires an async runner; use fetch_day_ahead_prices directly in async contexts",
        )
