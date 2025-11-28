from __future__ import annotations

import asyncio
import json
import logging
import os
import typing as t
from dataclasses import dataclass, field
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
    Response,
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
QUARTER_DAY_INTERVALS = 96
DEFAULT_GRANULARITY: t.Literal["quarter_hour"] = "quarter_hour"


@dataclass(frozen=True)
class PriceInterval:
    start_utc: datetime
    end_utc: datetime
    price_eur_per_mwh: float


@dataclass(frozen=True)
class DayPrices:
    market: str
    granularity: t.Literal["quarter_hour"]
    intervals: list[PriceInterval]
    published_at_utc: datetime | None


@dataclass
class DayMetadata:
    granularity: t.Literal["quarter_hour"] = DEFAULT_GRANULARITY
    expected_intervals: int = QUARTER_DAY_INTERVALS
    published_at_utc: datetime | None = None
    last_fetched_utc: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class Cache:
    intervals: list[PriceInterval] = field(default_factory=list)
    day_metadata: dict[date, DayMetadata] = field(default_factory=dict)
    last_refresh_utc: datetime | None = None

    def prune(self, now_utc: datetime | None = None) -> None:
        reference_local = (
            now_utc.astimezone(HELSINKI_TZ) if now_utc else datetime.now(tz=HELSINKI_TZ)
        )
        current_local_date = reference_local.date()
        keep_from = current_local_date
        self.intervals = [
            it for it in self.intervals if _local_date(it.start_utc) >= keep_from
        ]
        valid_dates = {_local_date(it.start_utc) for it in self.intervals}
        for day_key in list(self.day_metadata.keys()):
            if day_key not in valid_dates:
                self.day_metadata.pop(day_key, None)

    def upsert_day(self, target_date: date, day_prices: DayPrices) -> None:
        now_utc = datetime.now(UTC)
        self.intervals = [
            it for it in self.intervals if _local_date(it.start_utc) != target_date
        ]
        self.intervals.extend(day_prices.intervals)
        self.intervals.sort(key=lambda it: it.start_utc)
        self.day_metadata[target_date] = _metadata_from_day_prices(
            day_prices,
            fetched_at=now_utc,
        )
        self.last_refresh_utc = now_utc
        self.prune(now_utc)

    def intervals_for_date(self, target_date: date) -> list[PriceInterval]:
        return [it for it in self.intervals if _local_date(it.start_utc) == target_date]

    def has_complete_day(self, target_date: date) -> bool:
        meta = self.day_metadata.get(target_date)
        if not meta:
            return False
        intervals = self.intervals_for_date(target_date)
        return len(intervals) >= meta.expected_intervals


cache = Cache()
background_tasks: list[asyncio.Task] = []


def _track_background_task(
    coro: t.Coroutine[t.Any, t.Any, t.Any],
    name: str,
) -> asyncio.Task:
    task = asyncio.create_task(coro)
    task.set_name(name)
    background_tasks.append(task)

    def _cleanup(fut: asyncio.Task) -> None:
        if fut in background_tasks:
            background_tasks.remove(fut)

    task.add_done_callback(_cleanup)
    logger.debug("Scheduled background task %s", name)
    return task


def _local_date(dt: datetime) -> date:
    return dt.astimezone(HELSINKI_TZ).date()


def _metadata_from_day_prices(
    day_prices: DayPrices,
    *,
    fetched_at: datetime | None = None,
) -> DayMetadata:
    return DayMetadata(
        granularity=DEFAULT_GRANULARITY,
        expected_intervals=QUARTER_DAY_INTERVALS,
        published_at_utc=day_prices.published_at_utc,
        last_fetched_utc=fetched_at or datetime.now(UTC),
    )


def resolve_target_date(
    date_str: str,
    now_hel: datetime,
) -> tuple[date, bool]:
    today = now_hel.date()
    tomorrow = today + timedelta(days=1)

    if date_str == "today":
        return today, True
    if date_str == "tomorrow":
        return tomorrow, True

    try:
        target = datetime.fromisoformat(date_str).date()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid date") from exc

    persist = target in {today, tomorrow}
    return target, persist


def create_placeholder_intervals(
    target_date: date,
) -> list[PriceInterval]:
    step = timedelta(minutes=15)
    count = QUARTER_DAY_INTERVALS

    start_local = datetime.combine(
        target_date,
        datetime.min.time(),
        tzinfo=HELSINKI_TZ,
    )

    intervals: list[PriceInterval] = []
    for i in range(count):
        start_ts = (start_local + i * step).astimezone(UTC)
        intervals.append(
            PriceInterval(
                start_utc=start_ts,
                end_utc=start_ts + step,
                price_eur_per_mwh=0.0,
            ),
        )
    return intervals


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

    # Add cache-control middleware for proper browser caching
    @app.middleware("http")
    async def add_cache_control_headers(request, call_next):
        response = await call_next(request)
        path = request.url.path
        
        # Versioned static files (with ?v=... query param) can be cached long-term
        # since the version is part of the URL
        if path.startswith("/static/") and "v=" in str(request.url.query):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        # Non-versioned static files should be cached but revalidated
        elif path.startswith("/static/"):
            response.headers["Cache-Control"] = "public, max-age=3600, must-revalidate"
        # HTML pages and dynamic content should not be cached
        elif path in ["/", "/index"] or not path.startswith("/api"):
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        
        return response

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
            logger.info(
                "New SSE connection established, total clients: %d",
                len(cache_event_callbacks),
            )

            try:
                # Send initial version
                ver = os.environ.get("SPOT_VERSION", "dev")
                logger.info("Sending initial version to client: %s", ver)
                yield f'event: version_update\ndata: {{"type": "version", "version": "{ver}"}}\n\n'

                while True:
                    try:
                        # Wait for cache events or timeout after 30 seconds
                        event_data = await asyncio.wait_for(
                            event_queue.get(),
                            timeout=30.0,
                        )
                        # Extract event type from event_data
                        event_type = event_data.get("type", "message")
                        # Send with proper SSE event name
                        yield f"event: {event_type}\ndata: {json.dumps(event_data)}\n\n"
                    except TimeoutError:
                        # Send periodic version updates
                        ver = os.environ.get("SPOT_VERSION", "dev")
                        logger.debug(
                            "Sending periodic version update to client: %s",
                            ver,
                        )
                        yield f'event: version_update\ndata: {{"type": "version", "version": "{ver}"}}\n\n'
            finally:
                # Clean up callback when connection closes
                if on_cache_event in cache_event_callbacks:
                    cache_event_callbacks.remove(on_cache_event)
                logger.info(
                    "SSE connection closed, remaining clients: %d",
                    len(cache_event_callbacks),
                )

        return StreamingResponse(
            eventgen(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )

    from .entsoe import DataNotAvailable, fetch_day_ahead_prices

    async def fetch_prices_for_day(target_date: date) -> DayPrices:
        logger.info(
            f"Fetching prices for date: {target_date} (Helsinki time: {datetime.now(tz=HELSINKI_TZ)})",
        )
        prefer_15min = True
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
            granularity=DEFAULT_GRANULARITY,
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

    async def fetch_and_store_day(
        target_date: date,
        *,
        label: str,
    ) -> bool:
        logger.info("Fetching %s prices for %s", label, target_date)
        try:
            dp = await fetch_prices_for_day(target_date)
        except DataNotAvailable as exc:
            logger.info(
                "%s prices not available yet for %s: %s",
                label.title(),
                target_date,
                exc,
            )
            return False
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Failed to fetch %s prices for %s", label, target_date)
            return False

        cache.upsert_day(target_date, dp)
        logger.info(
            "Cached %s prices (%d intervals, %s granularity)",
            label,
            len(dp.intervals),
            dp.granularity,
        )
        await notify_cache_event(
            "day_updated",
            {
                "date": target_date.isoformat(),
                "granularity": dp.granularity,
                "intervals": len(dp.intervals),
            },
        )
        return True

    async def ensure_days_available(target_dates: list[date]) -> None:
        """Ensure specified local dates have fully cached data."""
        if not target_dates:
            return

        cache.prune(datetime.now(UTC))
        for target_date in target_dates:
            if cache.has_complete_day(target_date):
                continue
            await fetch_and_store_day(target_date, label=target_date.isoformat())

    async def ensure_day_available(
        target_date: date,
        *,
        persist: bool,
    ) -> tuple[list[PriceInterval], DayMetadata | None]:
        """Return all intervals for a date, fetching from ENTSOE if needed."""
        intervals = cache.intervals_for_date(target_date)
        metadata = cache.day_metadata.get(target_date)

        if intervals:
            return intervals, metadata

        logger.info("Cache miss for %s, fetching directly", target_date)
        try:
            dp = await fetch_prices_for_day(target_date)
        except DataNotAvailable as exc:
            logger.warning("Data not available for %s: %s", target_date, exc)
            return [], None
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Failed to fetch data for %s", target_date)
            raise

        if persist:
            cache.upsert_day(target_date, dp)
            metadata = cache.day_metadata.get(target_date)
        else:
            metadata = _metadata_from_day_prices(dp)

        return dp.intervals, metadata

    @app.get("/", response_class=HTMLResponse)
    async def home(
        request: Request,
        margin: float | None = Query(default=None),
    ) -> Response:
        # If no margin parameter provided, redirect to show default margin in URL
        if margin is None:
            from fastapi.responses import RedirectResponse

            redirect_url = f"/?margin={DEFAULT_MARGIN_CENTS_PER_KWH:.2f}"
            return RedirectResponse(url=redirect_url, status_code=302)

        # Validate margin parameter
        margin = validate_margin(margin)

        # Only ensure cache if it's not already populated (avoid delays on first page load)
        if not cache.intervals:
            logger.info("Cache not warmed up yet, ensuring cache for first page load")
            today = datetime.now(tz=HELSINKI_TZ).date()
            await ensure_days_available([today, today + timedelta(days=1)])
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

        if not cache.intervals:
            logger.warning(
                "Cache empty during price range calculation, ensuring cache",
            )
            today = datetime.now(tz=HELSINKI_TZ).date()
            await ensure_days_available([today, today + timedelta(days=1)])

        intervals_processed = 0

        for it in cache.intervals:
            spot_cents_with_vat = eur_mwh_to_cents_kwh(it.price_eur_per_mwh)
            total_price = spot_cents_with_vat + margin_cents

            if intervals_processed < 5 or total_price > global_max:
                logger.debug(
                    "Interval %d: spot=%.2f, total=%.2f (margin=%.2f)",
                    intervals_processed,
                    spot_cents_with_vat,
                    total_price,
                    margin_cents,
                )

            global_max = max(global_max, total_price)
            global_min = min(global_min, spot_cents_with_vat)
            intervals_processed += 1

        logger.debug("Processed %d intervals for scaling", intervals_processed)

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
        logger.debug("Scaling calculation details: intervals=%d", intervals_processed)

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

            now_hel = datetime.now(tz=HELSINKI_TZ)
            target, persist = resolve_target_date(date_str, now_hel)
            logger.debug(
                "/api/chart-data date=%s persist=%s margin=%.3f",
                target,
                persist,
                margin_cents,
            )

            # Calculate global price range for consistent scaling
            global_min_price, global_max_price = await calculate_global_price_range(
                margin_cents,
            )

            intervals, metadata = await ensure_day_available(
                target,
                persist=persist,
            )

            LOW_PRICE = 5.0  # cents/kWh
            HIGH_PRICE = 15.0  # cents/kWh

            chart_data = []

            # Process all intervals and filter by target date in Helsinki timezone
            if intervals:
                for it in intervals:
                    start_helsinki = it.start_utc.astimezone(HELSINKI_TZ)
                    if start_helsinki.date() != target:
                        continue

                    spot_cents_with_vat = eur_mwh_to_cents_kwh(it.price_eur_per_mwh)
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

                    quarter = start_helsinki.minute // 15
                    time_label = str(start_helsinki.hour * 4 + quarter)

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
            actual_granularity = DEFAULT_GRANULARITY
            actual_interval_count = len(chart_data)

            chart_data_dict = {row[0]: row for row in chart_data}
            for i in range(QUARTER_DAY_INTERVALS):
                time_key = str(i)
                if time_key in chart_data_dict:
                    complete_chart_data.append(chart_data_dict[time_key])
                else:
                    complete_chart_data.append([time_key, 0, 0, 0, margin_cents])

            # Handle case where no actual price data found
            if not chart_data:
                logger.warning("No price data found for date %s", target)
                return JSONResponse(
                    {
                        "data": complete_chart_data,
                        "maxPrice": global_max_price,
                        "minPrice": global_min_price,
                        "dateString": target.strftime("%A %m/%d/%Y"),
                        "dateIso": target.isoformat(),  # ISO format for client-side localization
                        "granularity": actual_granularity,
                        "intervalCount": actual_interval_count,
                        "expectedIntervalCount": len(complete_chart_data),
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
                    "intervalCount": actual_interval_count,
                    "expectedIntervalCount": len(complete_chart_data),
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

    def build_view_model(
        intervals: list[PriceInterval],
        margin_cents: float,
        granularity: t.Literal["quarter_hour"],
    ) -> dict[str, t.Any]:
        entries: list[dict[str, t.Any]] = []
        for it in intervals:
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
            "granularity": granularity,
        }

    @app.get("/partials/prices", response_class=HTMLResponse)
    async def partial_prices(
        request: Request,
        date: str,
        margin: float | None = None,
        role: str = "today",
    ) -> HTMLResponse:
        margin_cents = margin if margin is not None else DEFAULT_MARGIN_CENTS_PER_KWH
        # Validate margin parameter if provided
        if margin is not None:
            margin_cents = validate_margin(margin_cents)
        logger.debug(
            "/partials/prices date=%s role=%s margin=%.3f",
            date,
            role,
            margin_cents,
        )
        now_hel = datetime.now(tz=HELSINKI_TZ)
        target, persist = resolve_target_date(date, now_hel)
        intervals, metadata = await ensure_day_available(
            target,
            persist=persist,
        )
        filtered_intervals = [
            it for it in intervals if _local_date(it.start_utc) == target
        ]

        if not filtered_intervals:
            filtered_intervals = create_placeholder_intervals(target)
            metadata = metadata or DayMetadata(
                granularity=DEFAULT_GRANULARITY,
                expected_intervals=QUARTER_DAY_INTERVALS,
                published_at_utc=None,
                last_fetched_utc=datetime.now(UTC),
            )

        granularity = metadata.granularity if metadata else DEFAULT_GRANULARITY
        vm = build_view_model(filtered_intervals, margin_cents, granularity)
        return templates.TemplateResponse(
            "partials/prices.html",
            {
                "request": request,
                "vm": vm,
                "chart_role": role,
                "chart_date_iso": target.isoformat(),
            },
        )

    async def startup_tasks():
        """Warm cache on startup and launch background refresh loops."""

        async def warmup_cache() -> None:
            backoff = 10
            while True:
                try:
                    now = datetime.now(tz=HELSINKI_TZ)
                    targets = [now.date(), now.date() + timedelta(days=1)]
                    await ensure_days_available(targets)
                    if cache.has_complete_day(now.date()) and cache.has_complete_day(
                        now.date() + timedelta(days=1),
                    ):
                        logger.info("Initial cache warm-up succeeded")
                        return
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("Startup warm-up failed; retrying")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 300)

        async def fifteen_minute_health_check_loop():
            while True:
                try:
                    now = datetime.now(tz=HELSINKI_TZ)
                    cache.prune()
                    today_d = now.date()
                    tomorrow_d = today_d + timedelta(days=1)
                    missing_today = not cache.has_complete_day(today_d)
                    missing_tomorrow = not cache.has_complete_day(tomorrow_d)
                    targets: list[date] = []
                    if missing_today:
                        targets.append(today_d)
                    if missing_tomorrow:
                        targets.append(tomorrow_d)
                    if targets:
                        await ensure_days_available(targets)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("Health check loop encountered an error")
                await asyncio.sleep(15 * 60)

        async def afternoon_polling_loop():
            """Aggressively poll ENTSO-E between 14:00-14:30 Helsinki time."""
            while True:
                try:
                    now = datetime.now(tz=HELSINKI_TZ)
                    tomorrow_d = now.date() + timedelta(days=1)
                    in_window = now.hour == 14 and now.minute <= 30
                    if in_window and not cache.has_complete_day(tomorrow_d):
                        fetched = await fetch_and_store_day(
                            tomorrow_d,
                            label=tomorrow_d.isoformat(),
                        )
                        await asyncio.sleep(60 if not fetched else 120)
                        continue
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("Afternoon polling loop failed")
                await asyncio.sleep(60)

        _track_background_task(warmup_cache(), "warmup_cache")
        _track_background_task(fifteen_minute_health_check_loop(), "health_check_loop")
        _track_background_task(afternoon_polling_loop(), "afternoon_polling_loop")

    @app.on_event("startup")
    async def _on_startup() -> None:
        await startup_tasks()

    @app.on_event("shutdown")
    async def _on_shutdown() -> None:
        logger.info("Shutting down background tasks")
        for task in background_tasks:
            task.cancel()
        if background_tasks:
            await asyncio.gather(*background_tasks, return_exceptions=True)
        background_tasks.clear()

    return app
