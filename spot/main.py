from __future__ import annotations

import asyncio
import json
import logging
import os
import typing as t
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from dateutil import tz
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("spot")

ENTSOE_API_TOKEN = os.environ.get("ENTSOE_API_TOKEN")
DEFAULT_MARGIN_CENTS_PER_KWH = float(
    os.environ.get("DEFAULT_MARGIN_CENTS_PER_KWH", "0.60"),
)
VAT_RATE = 0.255
HELSINKI_TZ = tz.gettz("Europe/Helsinki")


@dataclass(frozen=True)
class PriceInterval:
    start_utc: datetime
    end_utc: datetime
    price_eur_per_mwh: float


@dataclass(frozen=True)
class DayPrices:
    market: str
    granularity: t.Literal["hour", "quarter_hour"]
    intervals: list[PriceInterval]
    published_at_utc: datetime | None


@dataclass
class Cache:
    today: DayPrices | None = None
    tomorrow: DayPrices | None = None
    last_refresh_utc: datetime | None = None


cache = Cache()


def _get_expected_intervals(granularity: t.Literal["hour", "quarter_hour"]) -> int:
    """Get expected number of intervals per day based on granularity.

    Note: This assumes standard 24-hour days. DST days (23/25 hours)
    will have different counts and should be handled separately.
    """
    if granularity == "hour":
        return 24  # 24 hours per day
    if granularity == "quarter_hour":
        return 96  # 4 * 24 = 96 fifteen-minute intervals per day
    raise ValueError(f"Unknown granularity: {granularity}")


class ProxyHeadersMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        # Respect X-Forwarded-Proto for URL generation
        xf_proto = request.headers.get("x-forwarded-proto")
        if xf_proto:
            scope = request.scope
            scope["scheme"] = xf_proto
        response = await call_next(request)
        return response


def create_app() -> FastAPI:
    if not ENTSOE_API_TOKEN:
        raise RuntimeError("ENTSOE_API_TOKEN is required")

    app = FastAPI(title="Spot is a dog")
    logger.info("Starting app: Spot is a dog")
    logger.info("Log level: %s", LOG_LEVEL)
    logger.info("Default margin (c/kWh): %s", DEFAULT_MARGIN_CENTS_PER_KWH)

    app.add_middleware(GZipMiddleware, minimum_size=1024)
    app.add_middleware(ProxyHeadersMiddleware)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["*"])

    templates = Jinja2Templates(directory="templates")
    app.mount("/static", StaticFiles(directory="static"), name="static")

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/favicon.ico")
    async def favicon() -> FileResponse:
        icon_path = Path(__file__).parent.parent.joinpath("static/spot-192.png")
        return FileResponse(
            icon_path,
            media_type="image/png",
            headers={"Cache-Control": "public, max-age=31536000"},
        )

    @app.get("/version")
    async def version() -> dict[str, str]:
        return {"version": os.environ.get("SPOT_VERSION", "dev")}

    @app.get("/events/version")
    async def version_events() -> StreamingResponse:
        async def eventgen():
            # Create a queue for this connection
            event_queue = asyncio.Queue()

            # Register callback to receive cache events
            async def on_cache_event(event_data):
                await event_queue.put(event_data)

            cache_event_callbacks.append(on_cache_event)

            try:
                # Send initial version
                ver = os.environ.get("SPOT_VERSION", "dev")
                yield f'data: {{"type": "version", "version": "{ver}"}}\n\n'

                while True:
                    try:
                        # Wait for cache events or timeout after 30 seconds
                        event_data = await asyncio.wait_for(
                            event_queue.get(),
                            timeout=30.0,
                        )
                        yield f"data: {json.dumps(event_data)}\n\n"
                    except TimeoutError:
                        # Send periodic version updates
                        ver = os.environ.get("SPOT_VERSION", "dev")
                        yield f'data: {{"type": "version", "version": "{ver}"}}\n\n'
            finally:
                # Clean up callback when connection closes
                if on_cache_event in cache_event_callbacks:
                    cache_event_callbacks.remove(on_cache_event)

        return StreamingResponse(eventgen(), media_type="text/event-stream")

    from .entsoe import DataNotAvailable, fetch_day_ahead_prices

    async def fetch_prices_for_day(target_date: date) -> DayPrices:
        logger.info(
            f"Fetching prices for date: {target_date} (Helsinki time: {datetime.now(tz=HELSINKI_TZ)})",
        )
        # Try 15-minute resolution first for dates after Oct 1, 2025
        # For testing: enable 15-minute simulation for current dates
        prefer_15min = target_date >= date(2025, 10, 1) or target_date >= date.today()
        if not ENTSOE_API_TOKEN:
            raise RuntimeError("ENTSOE_API_TOKEN is required")
        ds = await fetch_day_ahead_prices(
            ENTSOE_API_TOKEN,
            target_date,
            prefer_15min=prefer_15min,
        )
        intervals = [
            PriceInterval(p.start_utc, p.end_utc, p.price_eur_per_mwh)
            for p in ds.points
        ]
        logger.info(
            f"Fetched {len(intervals)} intervals ({ds.granularity}) for {target_date}, first interval: {intervals[0].start_utc.astimezone(HELSINKI_TZ) if intervals else 'None'}",
        )
        return DayPrices(
            market=ds.market,
            granularity=ds.granularity,
            intervals=intervals,
            published_at_utc=ds.published_at_utc,
        )

    # Cache event callbacks for notifying browsers
    cache_event_callbacks = []

    async def notify_cache_event(event_type: str, data: dict | None = None):
        """Notify all connected browsers about cache events"""
        event_data = {"type": event_type, "timestamp": datetime.now(UTC).isoformat()}
        if data:
            event_data.update(data)

        logger.info(
            f"Sending cache event to {len(cache_event_callbacks)} clients: {event_type}",
        )

        # Call all registered callbacks (WebSocket/SSE connections)
        for callback in cache_event_callbacks[
            :
        ]:  # Copy list to avoid modification during iteration
            try:
                await callback(event_data)
            except Exception as e:
                logger.warning(f"Failed to notify cache event callback: {e}")
                # Remove failed callbacks
                cache_event_callbacks.remove(callback)

    async def ensure_cache_now() -> None:
        # Minimal: populate today and attempt tomorrow
        now_hel = datetime.now(tz=HELSINKI_TZ)
        today_d = now_hel.date()
        logger.info(
            f"ensure_cache_now() called for {today_d} at Helsinki time {now_hel}",
        )

        # Check if we need to rotate cache at midnight or clean up contaminated data
        cache_rotated = False
        if cache.today is not None:
            # Debug: Check what dates are actually in today's cache
            today_dates = set()
            for it in cache.today.intervals:
                interval_date = it.start_utc.astimezone(HELSINKI_TZ).date()
                today_dates.add(interval_date)

            logger.info(
                f"Midnight rotation debug: today_d={today_d}, today cache contains dates: {sorted(today_dates)}",
            )

            # Check if cached "today" data contains the right date
            today_intervals = [
                it
                for it in cache.today.intervals
                if it.start_utc.astimezone(HELSINKI_TZ).date() == today_d
            ]
            total_intervals = len(cache.today.intervals)
            logger.info(
                f"Today cache: {len(today_intervals)}/{total_intervals} intervals match {today_d}",
            )

            # If cache is contaminated (contains wrong dates) or empty for today, fix it
            if len(today_intervals) == 0:
                # No intervals for today - try to rotate from tomorrow
                if cache.tomorrow is not None:
                    # Debug: Check what dates are actually in tomorrow's cache
                    tomorrow_dates = set()
                    for it in cache.tomorrow.intervals:
                        interval_date = it.start_utc.astimezone(HELSINKI_TZ).date()
                        tomorrow_dates.add(interval_date)

                    logger.info(
                        f"Midnight rotation debug: today_d={today_d}, tomorrow cache contains dates: {sorted(tomorrow_dates)}",
                    )

                    tomorrow_intervals = [
                        it
                        for it in cache.tomorrow.intervals
                        if it.start_utc.astimezone(HELSINKI_TZ).date() == today_d
                    ]
                    logger.info(
                        f"Tomorrow intervals matching {today_d}: {len(tomorrow_intervals)} out of {len(cache.tomorrow.intervals)} total",
                    )
                    if tomorrow_intervals:
                        logger.info(
                            f"Midnight transition: rotating tomorrow's cache to today ({today_d})",
                        )
                        # Create clean cache with only today's intervals
                        cache.today = replace(
                            cache.tomorrow,
                            intervals=tomorrow_intervals,
                        )
                        cache.tomorrow = None
                        cache_rotated = True
                        await notify_cache_event(
                            "cache_rotated",
                            {"new_today": today_d.isoformat()},
                        )
                    else:
                        logger.warning(
                            f"No tomorrow intervals match today's date {today_d}",
                        )
                        cache.today = None  # Clear contaminated cache
                else:
                    logger.warning(
                        "No today intervals and no tomorrow cache to rotate from",
                    )
                    cache.today = None  # Clear contaminated cache
            elif len(today_intervals) != total_intervals:
                # Cache is contaminated with intervals from other dates - clean it
                logger.info(
                    f"Cleaning contaminated today cache: keeping {len(today_intervals)}/{total_intervals} intervals for {today_d}",
                )
                cache.today = replace(cache.today, intervals=today_intervals)
            else:
                logger.debug(
                    f"Today cache is clean: {len(today_intervals)} intervals for {today_d}",
                )

        # Check if we need to fetch today's data
        need_today = True
        if cache.today is not None:
            # Check if any interval in cached data matches today's date
            for interval in cache.today.intervals:
                interval_date = interval.start_utc.astimezone(HELSINKI_TZ).date()
                if interval_date == today_d:
                    need_today = False
                    break

        if need_today:
            logger.info("Cache miss: Fetching today's prices for %s", today_d)
            try:
                cache.today = await fetch_prices_for_day(today_d)
                logger.info(
                    "Successfully cached today's prices (%d intervals)",
                    len(cache.today.intervals),
                )
                await notify_cache_event("today_updated", {"date": today_d.isoformat()})
            except DataNotAvailable as e:
                logger.warning(
                    "Today's prices not available yet for %s: %s; will retry",
                    today_d,
                    e,
                )
                cache.today = None
            except Exception as e:
                logger.error("Failed to fetch today's prices for %s: %s", today_d, e)
                cache.today = None
        else:
            logger.info(
                "Cache hit: Using cached today's prices (%d intervals)",
                len(cache.today.intervals),
            )

        # Check if we need to fetch tomorrow's data (either missing or incomplete)
        need_tomorrow = False
        tomorrow_d = today_d + timedelta(days=1)

        if cache.tomorrow is None:
            need_tomorrow = True
            logger.debug("Cache miss: No tomorrow data cached")
        else:
            # Check if tomorrow data is complete (all 24 hours)
            tomorrow_intervals = [
                it
                for it in cache.tomorrow.intervals
                if it.start_utc.astimezone(HELSINKI_TZ).date() == tomorrow_d
            ]
            # Check if tomorrow data is complete based on granularity
            expected_intervals = _get_expected_intervals(cache.tomorrow.granularity)
            if len(tomorrow_intervals) < expected_intervals:
                need_tomorrow = True
                logger.info(
                    "Incomplete tomorrow data: only %d/%d intervals cached, refetching",
                    len(tomorrow_intervals),
                    expected_intervals,
                )

        if need_tomorrow:
            logger.debug("Attempting to fetch tomorrow's prices for %s", tomorrow_d)
            try:
                cache.tomorrow = await fetch_prices_for_day(tomorrow_d)
                logger.info(
                    "Successfully cached tomorrow's prices (%d intervals)",
                    len(cache.tomorrow.intervals),
                )
                await notify_cache_event(
                    "tomorrow_updated",
                    {"date": tomorrow_d.isoformat()},
                )
            except DataNotAvailable:
                logger.debug("Tomorrow's prices not available yet")
                # Don't clear existing partial data - keep what we have
        else:
            logger.info(
                "Cache hit: Using cached tomorrow's prices (%d intervals)",
                len(cache.tomorrow.intervals),
            )

        cache.last_refresh_utc = datetime.now(UTC)

    @app.get("/", response_class=HTMLResponse)
    async def home(
        request: Request,
        margin: float | None = Query(default=None),
    ) -> HTMLResponse:
        # If no margin parameter provided, redirect to show default margin in URL
        if margin is None:
            from fastapi.responses import RedirectResponse

            redirect_url = f"/?margin={DEFAULT_MARGIN_CENTS_PER_KWH:.2f}"
            return RedirectResponse(url=redirect_url, status_code=302)

        # Validate margin parameter
        margin = validate_margin(margin)

        # Only ensure cache if it's not already populated (avoid delays on first page load)
        if cache.today is None:
            logger.info("Cache not warmed up yet, ensuring cache for first page load")
            await ensure_cache_now()
        else:
            logger.debug("Cache already warm, serving page immediately")

        return templates.TemplateResponse(
            "index.html",
            {
                "request": request,
                "app_name": "Spot is a dog",
                "margin_cents": margin,
                "app_version": os.environ.get("SPOT_VERSION", "dev"),
            },
        )

    @app.get("/api/prices", response_class=JSONResponse)
    async def api_prices(date_str: str) -> JSONResponse:
        target = datetime.fromisoformat(date_str).date()
        logger.debug("/api/prices date=%s", target)
        dp = await fetch_prices_for_day(target)
        return JSONResponse(
            {
                "market": dp.market,
                "granularity": dp.granularity,
                "intervals": [
                    {
                        "startTimeUtc": it.start_utc.isoformat(),
                        "endTimeUtc": it.end_utc.isoformat(),
                        "priceAmount": it.price_eur_per_mwh,
                        "priceCurrency": "EUR",
                        "unit": "MWh",
                    }
                    for it in dp.intervals
                ],
            },
        )

    async def calculate_global_price_range(margin_cents: float) -> tuple[float, float]:
        """Calculate global min/max price range for consistent chart scaling"""

        global_max = float("-inf")
        global_min = float("inf")

        # Cache should already be populated from startup
        if cache.today is None:
            logger.warning(
                "Cache not warm during price range calculation, ensuring cache",
            )
            await ensure_cache_now()

        datasets_checked = 0
        intervals_processed = 0

        # Check both today and tomorrow data for global range
        for name, dp in [("today", cache.today), ("tomorrow", cache.tomorrow)]:
            if dp is None:
                logger.debug(f"No data for {name}")
                continue

            datasets_checked += 1
            logger.debug(f"Processing {name} data with {len(dp.intervals)} intervals")

            for it in dp.intervals:
                # Convert EUR/MWh to cents/kWh (with VAT included)
                spot_cents_with_vat = eur_mwh_to_cents_kwh(it.price_eur_per_mwh)

                # Calculate the total price (spot + margin) that will be displayed
                total_price = spot_cents_with_vat + margin_cents

                # Debug logging for scaling investigation
                if (
                    intervals_processed < 5 or total_price > global_max
                ):  # Log first few and any new maximums
                    logger.debug(
                        f"Price interval {intervals_processed}: spot={spot_cents_with_vat:.2f}, total={total_price:.2f} (margin={margin_cents:.2f})",
                    )

                # Track the actual range of total prices
                global_max = max(global_max, total_price)
                global_min = min(
                    global_min,
                    spot_cents_with_vat,
                )  # Spot price can be negative, margin is always added on top

                intervals_processed += 1

        logger.debug(
            f"Processed {datasets_checked} datasets, {intervals_processed} intervals",
        )

        # If no data found, use reasonable defaults
        if global_min == float("inf") or global_max == float("-inf"):
            logger.warning(
                "No price data found for global range calculation, using defaults",
            )
            global_min = 0.0
            global_max = 25.0

        # Round to 5-cent increments with minimum of 15 cents
        # Maximum: always at least 15 cents, or round UP to next 5 cents above highest price
        if global_max <= 0:
            max_price_rounded = 15  # Minimum scale of 15 cents
        else:
            # Calculate rounded price based on actual max
            if global_max % 5 == 0:
                calculated_max = int(global_max) + 5
            else:
                calculated_max = ((int(global_max) // 5) + 1) * 5

            # Ensure minimum of 15 cents
            max_price_rounded = max(15, calculated_max)

        # Minimum: round DOWN to next 1 cent below lowest price, or 0 for positive prices
        if global_min >= 0:
            min_price_rounded = 0  # Start from 0 for positive prices
        else:
            # For negative prices, round down to next 1-cent boundary
            import math

            if global_min % 1 == 0:
                min_price_rounded = int(global_min) - 1
            else:
                min_price_rounded = math.floor(global_min)

        logger.info(
            f"Global price range: {global_min:.2f} -> {global_max:.2f}, rounded: {min_price_rounded} -> {max_price_rounded} (margin: {margin_cents:.3f})",
        )
        logger.info(
            f"Y-axis range calculation: min={global_min:.2f} -> {min_price_rounded}, max={global_max:.2f} -> {max_price_rounded}",
        )
        logger.debug(
            f"Scaling calculation details: datasets={datasets_checked}, intervals={intervals_processed}",
        )

        return min_price_rounded, max_price_rounded

    @app.get("/api/chart-data", response_class=JSONResponse)
    async def api_chart_data(
        date_str: str,
        margin: float | None = Query(default=None),
    ) -> JSONResponse:
        """API endpoint that provides data in Google Charts format like the Angular component"""
        try:
            margin_cents = (
                margin if margin is not None else DEFAULT_MARGIN_CENTS_PER_KWH
            )
            # Validate margin parameter if provided
            if margin is not None:
                margin_cents = validate_margin(margin_cents)

            # Handle special date strings and convert to actual dates in Helsinki timezone
            now_hel = datetime.now(tz=HELSINKI_TZ)
            if date_str == "today":
                target = now_hel.date()
                logger.debug(
                    "/api/chart-data date=today (%s) margin=%.3f",
                    target,
                    margin_cents,
                )
            elif date_str == "tomorrow":
                target = (now_hel + timedelta(days=1)).date()
                logger.debug(
                    "/api/chart-data date=tomorrow (%s) margin=%.3f",
                    target,
                    margin_cents,
                )
            else:
                target = datetime.fromisoformat(date_str).date()
                logger.debug(
                    "/api/chart-data date=%s margin=%.3f",
                    target,
                    margin_cents,
                )

            # Get all available data from cache first (cache should be warm from startup)
            # But also check if cache contains stale data (e.g., after midnight)
            cache_needs_refresh = False
            if cache.today is None:
                logger.warning(
                    "Cache not warm during chart data request, ensuring cache",
                )
                cache_needs_refresh = True
            else:
                # Check if today's cache contains data for the current date
                today_date = now_hel.date()
                today_intervals_for_current_date = [
                    it
                    for it in cache.today.intervals
                    if it.start_utc.astimezone(HELSINKI_TZ).date() == today_date
                ]
                if len(today_intervals_for_current_date) == 0:
                    logger.warning(
                        f"Today's cache contains no data for current date {today_date}, ensuring cache",
                    )
                    cache_needs_refresh = True
                else:
                    logger.debug("Using warm cache for chart data")

            if cache_needs_refresh:
                await ensure_cache_now()

            # Calculate global price range for consistent scaling
            global_min_price, global_max_price = await calculate_global_price_range(
                margin_cents,
            )

            # Determine which data to use based on the requested date
            now_hel = datetime.now(tz=HELSINKI_TZ)
            today_date = now_hel.date()
            tomorrow_date = today_date + timedelta(days=1)

            # Use cache data directly - cache should already be populated by ensure_cache_now()
            dp = None
            if target == today_date and cache.today:
                logger.debug(f"Using today's cache for {target}")
                dp = cache.today
            elif target == tomorrow_date and cache.tomorrow:
                logger.debug(f"Using tomorrow's cache for {target}")
                dp = cache.tomorrow
            else:
                # Only fetch directly if not today/tomorrow or cache miss
                logger.warning(
                    f"Cache miss for {target}, fetching directly from ENTSO-E",
                )
                try:
                    dp = await fetch_prices_for_day(target)
                except Exception as e:
                    logger.error(f"Failed to fetch data for {target}: {e}")
                    dp = None

            LOW_PRICE = 5.0  # cents/kWh
            HIGH_PRICE = 15.0  # cents/kWh

            chart_data = []

            # Process all intervals and filter by target date in Helsinki timezone
            if dp and dp.intervals:
                for it in dp.intervals:
                    # Convert to Helsinki timezone
                    start_helsinki = it.start_utc.astimezone(HELSINKI_TZ)
                    interval_date = start_helsinki.date()

                    # Skip intervals that don't match the target date
                    if interval_date != target:
                        continue

                    # Convert EUR/MWh to cents/kWh (with VAT already included)
                    spot_cents_with_vat = eur_mwh_to_cents_kwh(it.price_eur_per_mwh)

                    # Split electricity price into low/medium/high buckets like Angular component
                    low_electricity = (
                        spot_cents_with_vat if spot_cents_with_vat < LOW_PRICE else 0
                    )
                    medium_electricity = (
                        spot_cents_with_vat
                        if LOW_PRICE <= spot_cents_with_vat < HIGH_PRICE
                        else 0
                    )
                    high_electricity = (
                        spot_cents_with_vat if spot_cents_with_vat >= HIGH_PRICE else 0
                    )

                    # Create time label based on granularity
                    if dp.granularity == "quarter_hour":
                        # For 15-minute intervals, use sequential integer indices
                        # This prevents Google Charts from generating intermediate ticks
                        quarter = start_helsinki.minute // 15
                        time_label = str(start_helsinki.hour * 4 + quarter)
                    else:
                        # For hourly intervals, use hour only
                        time_label = str(start_helsinki.hour)

                    chart_data.append(
                        [
                            time_label,
                            low_electricity,
                            medium_electricity,
                            high_electricity,
                            margin_cents,
                        ],
                    )

            # Sort chart data by time to ensure proper order
            # Both hourly and 15-minute data now use integer indices, so sort numerically
            chart_data.sort(key=lambda x: int(x[0]))

            # Ensure all intervals are represented for consistent chart layout
            complete_chart_data = []
            actual_granularity = "hour"  # Default fallback

            if dp and dp.granularity == "quarter_hour":
                # For 15-minute data, create complete 96-interval chart
                actual_granularity = "quarter_hour"
                chart_data_dict = {row[0]: row for row in chart_data}

                for i in range(96):
                    time_key = str(i)
                    if time_key in chart_data_dict:
                        # Use existing data
                        complete_chart_data.append(chart_data_dict[time_key])
                    else:
                        # Fill missing interval with zeros (margin still applies)
                        complete_chart_data.append(
                            [time_key, 0, 0, 0, margin_cents],
                        )
            else:
                # For hourly data or when no data available, create complete 24-hour chart
                actual_granularity = "hour"
                chart_data_dict = (
                    {int(row[0]): row for row in chart_data} if chart_data else {}
                )

                for hour in range(24):
                    hour_str = str(hour)
                    if hour in chart_data_dict:
                        # Use existing data
                        complete_chart_data.append(chart_data_dict[hour])
                    else:
                        # Fill missing hour with zeros (margin still applies)
                        complete_chart_data.append([hour_str, 0, 0, 0, margin_cents])

            # Handle case where no actual price data found
            if not chart_data:
                logger.warning(f"No price data found for date {target}")
                return JSONResponse(
                    {
                        "data": complete_chart_data,
                        "maxPrice": global_max_price,
                        "minPrice": global_min_price,
                        "dateString": target.strftime("%A %m/%d/%Y"),
                        "dateIso": target.isoformat(),  # ISO format for client-side localization
                        "granularity": actual_granularity,  # Use the determined granularity
                        "intervalCount": len(complete_chart_data),
                        "error": "No price data available for this date",
                    },
                )

            return JSONResponse(
                {
                    "data": complete_chart_data,
                    "maxPrice": global_max_price,
                    "minPrice": global_min_price,
                    "dateString": target.strftime("%A %m/%d/%Y"),
                    "dateIso": target.isoformat(),  # ISO format for client-side localization
                    "granularity": actual_granularity,
                    "intervalCount": len(complete_chart_data),
                },
            )
        except Exception as e:
            logger.error(f"Error in chart-data endpoint: {e}")
            raise HTTPException(
                status_code=500,
                detail=f"Error fetching chart data: {e!s}",
            )

    def eur_mwh_to_cents_kwh(eur_per_mwh: float) -> float:
        # 1 MWh = 1000 kWh; EUR/MWh to EUR/kWh then to cents; include VAT
        eur_per_kwh = eur_per_mwh / 1000.0
        cents_per_kwh = eur_per_kwh * 100.0
        with_vat = cents_per_kwh * (1.0 + VAT_RATE)
        return with_vat

    def color_for_spot_cents(spot_cents: float) -> str:
        if spot_cents < 5.0:
            return "green"
        if spot_cents < 15.0:
            return "yellow"
        return "red"

    def validate_margin(margin: float) -> float:
        """Validate margin parameter and ensure it's within acceptable range"""
        if margin < -5.0:
            raise HTTPException(
                status_code=400,
                detail="Margin cannot be less than -5.0 cents per kWh",
            )
        if margin > 5.0:
            raise HTTPException(
                status_code=400,
                detail="Margin cannot be greater than 5.0 cents per kWh",
            )
        return margin

    def build_view_model(dp: DayPrices, margin_cents: float) -> dict[str, t.Any]:
        entries: list[dict[str, t.Any]] = []
        for it in dp.intervals:
            spot_cents = eur_mwh_to_cents_kwh(it.price_eur_per_mwh)
            total_cents = max(0.0, spot_cents) + max(0.0, margin_cents)
            entries.append(
                {
                    "startUtc": it.start_utc,
                    "endUtc": it.end_utc,
                    "spotCents": spot_cents,
                    "marginCents": margin_cents,
                    "totalCents": total_cents,
                    "color": color_for_spot_cents(spot_cents),
                },
            )
        max_total = max((e["totalCents"] for e in entries), default=1.0) or 1.0
        return {
            "entries": entries,
            "maxTotal": max_total,
            "granularity": dp.granularity,
        }

    @app.get("/partials/prices", response_class=HTMLResponse)
    async def partial_prices(
        request: Request,
        date: str,
        margin: float | None = None,
    ) -> HTMLResponse:
        margin_cents = margin if margin is not None else DEFAULT_MARGIN_CENTS_PER_KWH
        # Validate margin parameter if provided
        if margin is not None:
            margin_cents = validate_margin(margin_cents)
        logger.debug("/partials/prices date=%s margin=%.3f", date, margin_cents)
        now_hel = datetime.now(tz=HELSINKI_TZ)
        base_date = now_hel.date()
        if date == "today":
            # Check if today's cache contains data for the current date
            if cache.today is not None:
                today_intervals_for_current_date = [
                    it
                    for it in cache.today.intervals
                    if it.start_utc.astimezone(HELSINKI_TZ).date() == base_date
                ]
                if len(today_intervals_for_current_date) > 0:
                    dp = cache.today
                else:
                    logger.warning(
                        f"Today's cache contains no data for current date {base_date}, fetching fresh data",
                    )
                    dp = await fetch_prices_for_day(base_date)
            else:
                dp = await fetch_prices_for_day(base_date)
        elif date == "tomorrow":
            dp = cache.tomorrow
            if dp is None:
                # Build margin-only skeleton for tomorrow
                logger.info(
                    "Tomorrow not yet published; rendering margin-only skeleton",
                )
                tomorrow_date = base_date + timedelta(days=1)
                start_utc = datetime.combine(
                    tomorrow_date,
                    datetime.min.time(),
                    tzinfo=UTC,
                )

                # Use 15-minute intervals if date is after Oct 1, 2025 or for testing current dates
                if tomorrow_date >= date(2025, 10, 1) or tomorrow_date >= date.today():
                    # Create 96 fifteen-minute intervals
                    intervals = [
                        PriceInterval(
                            start_utc + i * timedelta(minutes=15),
                            start_utc + (i + 1) * timedelta(minutes=15),
                            0.0,
                        )
                        for i in range(96)
                    ]
                    granularity = "quarter_hour"
                else:
                    # Create 24 hourly intervals
                    intervals = [
                        PriceInterval(
                            start_utc + i * timedelta(hours=1),
                            start_utc + (i + 1) * timedelta(hours=1),
                            0.0,
                        )
                        for i in range(24)
                    ]
                    granularity = "hour"

                dp = DayPrices(
                    market="FI",
                    granularity=granularity,
                    intervals=intervals,
                    published_at_utc=None,
                )
        else:
            try:
                target = datetime.fromisoformat(date).date()
            except Exception as exc:
                logger.warning("Invalid date param: %s", date)
                raise HTTPException(status_code=400, detail="Invalid date") from exc
            dp = await fetch_prices_for_day(target)

        vm = build_view_model(dp, margin_cents)
        return templates.TemplateResponse(
            "partials/prices.html",
            {
                "request": request,
                "vm": vm,
            },
        )

    async def startup_tasks():
        # Initial fetch with retry/backoff until we have today's data
        backoff = 10
        while True:
            try:
                logger.info("Startup fetch attempt (backoff=%ss)", backoff)
                await ensure_cache_now()
                if cache.today is not None:
                    logger.info("Startup fetch succeeded; cache is warm")
                    break
            except Exception:
                logger.exception("Startup fetch failed; will retry")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 300)

        async def intelligent_polling_loop():
            """Intelligent polling that adapts based on data availability and time"""
            consecutive_failures = 0
            last_failure_time = None

            while True:
                now = datetime.now(tz=HELSINKI_TZ)
                today_d = now.date()
                tomorrow_d = today_d + timedelta(days=1)

                # Assess current data state with date validation
                has_today = (
                    cache.today is not None
                    and any(
                        it.start_utc.astimezone(HELSINKI_TZ).date() == today_d
                        for it in cache.today.intervals
                    )
                    if cache.today
                    else False
                )

                # Check if we have complete tomorrow data (all 24 hours)
                has_tomorrow = False
                if cache.tomorrow is not None:
                    tomorrow_intervals = [
                        it
                        for it in cache.tomorrow.intervals
                        if it.start_utc.astimezone(HELSINKI_TZ).date() == tomorrow_d
                    ]
                    # Consider tomorrow data complete based on granularity
                    expected_intervals = _get_expected_intervals(
                        cache.tomorrow.granularity,
                    )
                    has_tomorrow = len(tomorrow_intervals) >= expected_intervals

                # Determine urgency based on missing critical data
                missing_today = not has_today
                missing_tomorrow_after_2pm = not has_tomorrow and now.hour >= 14

                # Apply exponential backoff for persistent failures
                base_interval = 60
                if consecutive_failures > 0:
                    backoff_multiplier = min(2**consecutive_failures, 16)  # Cap at 16x
                    logger.debug(
                        f"Applying backoff multiplier {backoff_multiplier}x due to {consecutive_failures} consecutive failures",
                    )
                else:
                    backoff_multiplier = 1

                # Calculate polling interval based on situation
                if missing_today:
                    # Critical: Missing today's data - aggressive polling with backoff
                    base_interval = 60  # 1 minute base
                    poll_interval = base_interval * backoff_multiplier
                    logger.warning(
                        f"Missing today's data ({today_d}) - polling every {poll_interval}s",
                    )
                elif missing_tomorrow_after_2pm:
                    # Important: Missing tomorrow's data after expected publication
                    base_interval = 300  # 5 minutes base
                    poll_interval = base_interval * backoff_multiplier
                    logger.warning(
                        f"Missing tomorrow's data ({tomorrow_d}) after 14:00 - polling every {poll_interval}s",
                    )
                elif not has_tomorrow and (
                    (now.hour == 13 and now.minute >= 50)
                    or (now.hour == 14)
                    or (now.hour == 15 and now.minute <= 30)
                ):
                    # Expected publication window - frequent polling only if we DON'T have tomorrow's data yet
                    base_interval = 180  # 3 minutes base
                    poll_interval = base_interval * min(
                        backoff_multiplier,
                        4,
                    )  # Less aggressive backoff
                    logger.debug(
                        "In tomorrow publication window, waiting for data - frequent polling",
                    )
                elif has_today and has_tomorrow:
                    # All good - infrequent maintenance polling
                    poll_interval = 900  # 15 minutes (no backoff needed)
                    logger.debug("All data cached - maintenance polling")
                else:
                    # Default case - moderate polling
                    poll_interval = 600  # 10 minutes (no backoff needed)
                    logger.debug("Standard polling interval")

                # Attempt cache update
                try:
                    old_today = cache.today
                    old_tomorrow = cache.tomorrow

                    await ensure_cache_now()

                    # Log any changes
                    if old_today is None and cache.today is not None:
                        logger.info(
                            f"Successfully retrieved today's prices ({today_d})",
                        )
                    if old_tomorrow is None and cache.tomorrow is not None:
                        logger.info(
                            f"Successfully retrieved tomorrow's prices ({tomorrow_d})",
                        )

                    # Check for data updates (republications) for both today and tomorrow
                    for name, old_data, new_data in [
                        ("today", old_today, cache.today),
                        ("tomorrow", old_tomorrow, cache.tomorrow),
                    ]:
                        if old_data is not None and new_data is not None:
                            # Check if data was republished
                            if old_data.published_at_utc != new_data.published_at_utc:
                                logger.info(
                                    f"{name.title()}'s price data was republished",
                                )
                                # Send update event to refresh charts with new scaling
                                await notify_cache_event(
                                    f"{name}_updated",
                                    {
                                        "date": (
                                            today_d if name == "today" else tomorrow_d
                                        ).isoformat(),
                                        "reason": "republished",
                                    },
                                )
                            # Check if interval count changed
                            elif len(old_data.intervals) != len(new_data.intervals):
                                logger.info(
                                    f"{name.title()}'s price data changed (different interval count)",
                                )
                                await notify_cache_event(
                                    f"{name}_updated",
                                    {
                                        "date": (
                                            today_d if name == "today" else tomorrow_d
                                        ).isoformat(),
                                        "reason": "interval_count_changed",
                                    },
                                )
                            # Check if actual price values changed
                            elif any(
                                old_it.price_eur_per_mwh != new_it.price_eur_per_mwh
                                for old_it, new_it in zip(
                                    old_data.intervals,
                                    new_data.intervals,
                                    strict=False,
                                )
                            ):
                                logger.info(f"{name.title()}'s price values changed")
                                await notify_cache_event(
                                    f"{name}_updated",
                                    {
                                        "date": (
                                            today_d if name == "today" else tomorrow_d
                                        ).isoformat(),
                                        "reason": "price_values_changed",
                                    },
                                )

                    # Reset failure counter on success
                    if consecutive_failures > 0:
                        logger.info(
                            f"Polling recovered after {consecutive_failures} failures",
                        )
                        consecutive_failures = 0
                        last_failure_time = None

                except Exception as e:
                    consecutive_failures += 1
                    last_failure_time = now
                    logger.exception(
                        f"Polling attempt failed (failure #{consecutive_failures}): {e}",
                    )

                # Sleep until next poll
                await asyncio.sleep(poll_interval)

        async def midnight_cache_rotation_loop():
            """Background task to ensure cache rotation happens at midnight even if no requests come in"""
            while True:
                now = datetime.now(tz=HELSINKI_TZ)
                # Calculate seconds until next midnight in Helsinki timezone
                # Get the next day and create midnight properly with timezone
                next_day = now.date() + timedelta(days=1)
                next_midnight = datetime.combine(
                    next_day,
                    datetime.min.time(),
                    tzinfo=HELSINKI_TZ,
                )
                seconds_until_midnight = (next_midnight - now).total_seconds()

                # Sleep until midnight (with small buffer)
                await asyncio.sleep(max(1, seconds_until_midnight - 30))

                # Check cache rotation at midnight
                logger.info(
                    f"Midnight timer: checking cache rotation at {datetime.now(tz=HELSINKI_TZ)}",
                )
                await ensure_cache_now()
                logger.info("Midnight cache rotation check completed")

                # Sleep a bit to avoid multiple triggers
                await asyncio.sleep(60)

        asyncio.create_task(intelligent_polling_loop())
        asyncio.create_task(midnight_cache_rotation_loop())

    @app.on_event("startup")
    async def _on_startup() -> None:
        await startup_tasks()

    return app
