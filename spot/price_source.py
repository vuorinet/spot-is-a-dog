from __future__ import annotations

import logging
from datetime import date

from . import elering, entsoe, nordpool
from .entsoe import DataNotAvailable, DaySeries

logger = logging.getLogger("spot.price_source")

_FALLBACK_SOURCES = (
    ("Elering", elering),
    ("Nord Pool", nordpool),
)


class AllPriceSourcesFailedError(RuntimeError): ...


async def fetch_day_ahead_prices(
    token: str,
    target_date: date,
    prefer_15min: bool = False,
) -> DaySeries:
    """Fetch day-ahead prices, falling back across sources if ENTSO-E is down.

    Tries ENTSO-E first (the primary, official EU transparency platform).
    If it fails (e.g. maintenance, outage), falls back in order to Elering
    (Estonia's TSO, re-publishes Nord Pool data) and then the Nord Pool
    Data Portal directly.

    A source raising DataNotAvailable (day not published yet) is not
    treated as a fallback trigger the same way a hard failure is — every
    source is still tried, since publication timing can differ slightly
    between vendors, but if none of them have the data yet that "not
    published" signal is what gets raised to the caller (same as calling
    entsoe.fetch_day_ahead_prices directly), not a fallback-exhausted
    error.

    Raises:
        DataNotAvailable: If no source has published this day yet.
        AllPriceSourcesFailedError: If at least one source hard-failed and
            none succeeded.
    """
    not_yet_available: list[str] = []
    failures: list[str] = []

    try:
        return await entsoe.fetch_day_ahead_prices(
            token,
            target_date,
            prefer_15min=prefer_15min,
        )
    except DataNotAvailable as exc:
        not_yet_available.append(f"ENTSO-E: {exc}")
    except Exception as exc:  # noqa: BLE001 - any failure here falls through to fallback sources
        logger.warning("ENTSO-E price fetch failed for %s: %s", target_date, exc)
        failures.append(f"ENTSO-E: {exc}")

    for name, source_module in _FALLBACK_SOURCES:
        try:
            series = await source_module.fetch_day_ahead_prices(target_date)
        except DataNotAvailable as exc:
            not_yet_available.append(f"{name}: {exc}")
            continue
        except Exception as exc:  # noqa: BLE001 - any failure here falls through to the next source
            logger.warning("%s price fetch failed for %s: %s", name, target_date, exc)
            failures.append(f"{name}: {exc}")
            continue

        logger.warning("Using fallback price source for %s: %s", target_date, name)
        return series

    if not failures:
        # Every source agreed the data just isn't published yet - preserve
        # that as the normal, expected signal rather than a hard error.
        msg = "; ".join(not_yet_available)
        raise DataNotAvailable(msg)

    msg = (
        f"All price sources failed for {target_date}: "
        + "; ".join([*failures, *not_yet_available])
    )
    raise AllPriceSourcesFailedError(msg)
