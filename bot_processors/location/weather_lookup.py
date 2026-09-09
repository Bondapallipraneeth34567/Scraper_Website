"""Weather lookup: a real LLM tool call. The LLM extracts whatever place
name the caller said (or asks for one if they didn't say it) and calls
get_weather(location=...) directly — no location is ever hardcoded here or
resolved via a fixed alias table.

Unlike bot_processors.pricing.price_lookup, this fetches live, per-call —
OpenWeatherMap is a direct JSON API (no browser, no cascading form), so a
lookup is a sub-second network round trip rather than the several seconds a
single agmarknet.gov.in commodity scrape takes. No background job, no cache
file.

Unlike RAGInjector (bot_processors/rag/rag.py) and PriceLookupInjector
(bot_processors/pricing/price_lookup.py), this is not a FrameProcessor that injects
a synthetic system message ahead of every turn — it's a genuine pipecat
"direct function" tool (see pipecat.adapters.schemas.direct_function),
registered on the LLMContext's `tools=[...]` list in Bot.py. The LLM decides
on its own, per turn, whether the caller's words justify calling it, and
with what argument; there is no keyword/intent pre-detection step here at
all, so there's nothing that needs a district dictionary to resolve against.

Source: OpenWeatherMap only. (An IMD (India Meteorological Department)
primary tier existed here previously — removed 2026-08-25. It was never
live in production: IMD_API_KEY was always blank, so every real call was
already falling straight through to OpenWeatherMap; this removal doesn't
change caller-facing behavior.)
"""

import asyncio
import os
import time
from datetime import datetime, timezone

import requests
from loguru import logger

from pipecat.services.llm_service import FunctionCallParams

from bot_processors.calls.call_db import log_tool_call
from bot_processors.core.task_tracker import track_task

_OPENWEATHERMAP_API_KEY = os.getenv("OPENWEATHERMAP_API_KEY", "")


# ──────────────────────────────────────────────────────────────────────────────
# OpenWeatherMap

def _weather_by_coords(lat: float, lon: float) -> dict:
    resp = requests.get(
        "https://api.openweathermap.org/data/2.5/weather",
        params={"lat": lat, "lon": lon, "appid": _OPENWEATHERMAP_API_KEY, "units": "metric"},
        timeout=5,
    )
    resp.raise_for_status()
    data = resp.json()
    return {
        "source": "OpenWeatherMap",
        "temperature_c": data["main"]["temp"],
        "humidity_pct": data["main"]["humidity"],
        "precipitation_mm": data.get("rain", {}).get("1h", 0.0),
        "wind_kmh": round(data["wind"]["speed"] * 3.6, 1),
        "description": data["weather"][0]["description"],
        "as_of": datetime.now(timezone.utc).isoformat(),
    }


def _geocode(location: str) -> tuple[float, float] | None:
    """Falls back to OpenWeatherMap's own Geocoding API, which does fuzzy
    name matching against a much broader gazetteer than the direct-weather
    endpoint's `q` param — the direct endpoint 404s on plenty of small
    Indian villages/mandals that this API still resolves. Returns the first
    (best) match's coordinates, or None if it has nothing either."""
    resp = requests.get(
        "https://api.openweathermap.org/geo/1.0/direct",
        params={"q": f"{location},IN", "limit": 1, "appid": _OPENWEATHERMAP_API_KEY},
        timeout=5,
    )
    resp.raise_for_status()
    matches = resp.json()
    if not matches:
        return None
    return matches[0]["lat"], matches[0]["lon"]


def _fetch_openweathermap(location: str) -> dict | None:
    """OpenWeatherMap's `q` param already accepts any free-text place name
    (city, village, town) worldwide — this is why no fixed location table is
    needed anywhere in this refactor. `,IN` is appended to bias results
    toward India, since this bot's callers are all Indian farmers and a bare
    place name like "Guntur" could otherwise match a same-named place
    elsewhere.

    A direct name query still 404s on plenty of small villages/mandals that
    aren't in the weather endpoint's own place database (flagged live
    2026-07-29 by a tester: "Weather information is unavailable for a few
    locations"). Falling back to the Geocoding API's fuzzier gazetteer, then
    querying weather by coordinates, recovers most of those without hardcoding
    any location."""
    if not _OPENWEATHERMAP_API_KEY:
        return None
    try:
        resp = requests.get(
            "https://api.openweathermap.org/data/2.5/weather",
            params={"q": f"{location},IN", "appid": _OPENWEATHERMAP_API_KEY, "units": "metric"},
            timeout=5,
        )
        if resp.status_code == 404:
            raise requests.HTTPError(response=resp)
        resp.raise_for_status()
        data = resp.json()
        return {
            "source": "OpenWeatherMap",
            "temperature_c": data["main"]["temp"],
            "humidity_pct": data["main"]["humidity"],
            "precipitation_mm": data.get("rain", {}).get("1h", 0.0),
            "wind_kmh": round(data["wind"]["speed"] * 3.6, 1),
            "description": data["weather"][0]["description"],
            "as_of": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        logger.info(f"weather_lookup: direct name lookup failed for {location!r} ({e}), trying geocoding fallback")
        try:
            coords = _geocode(location)
            if coords is None:
                logger.warning(f"⚠️ weather_lookup: no data at all (direct or geocoded) for {location!r}")
                return None
            return _weather_by_coords(*coords)
        except Exception as e2:
            logger.warning(f"⚠️ weather_lookup: geocoding fallback also failed for {location!r}: {e2}")
            return None


async def fetch_weather(location: str) -> dict | None:
    """requests is a synchronous library, run off the event loop via
    asyncio.to_thread — same pattern as bot_processors/calls/escalation.py's
    outbound HTTP call."""
    return await asyncio.to_thread(_fetch_openweathermap, location)


def make_get_weather(serializer=None):
    """Builds the get_weather direct-function tool, closing over this call's
    serializer so tool-call outcomes can still be logged via log_tool_call
    (bot_processors/calls/call_db.py) — Bot.py builds one LLMContext/serializer per
    call, so each call gets its own get_weather closure with the right
    call_id baked in. The LLM only ever sees the plain get_weather(location)
    signature below; the closure is invisible to it.
    """

    async def get_weather(params: FunctionCallParams, location: str) -> None:
        """Get the current weather for a place the caller named.

        Call this whenever the caller asks about weather, rain, temperature,
        humidity, wind, or storms. Only call this once you know a specific
        city, village, town, or district — if the caller hasn't said one yet,
        do not call this function; ask them which city, village, or location
        they mean first instead.

        Args:
            location: The city, village, town, or district the caller said,
                as close to their own words as possible (for example
                "Hyderabad", "Warangal", "Ongole"). Never guess or invent a
                location that the caller didn't say.
        """
        _t0 = time.monotonic()
        result = await fetch_weather(location)
        exec_ms = int((time.monotonic() - _t0) * 1000)

        if result:
            await params.result_callback({
                "location": location,
                "source": result["source"],
                "description": result["description"],
                "temperature_c": result["temperature_c"],
                "humidity_pct": result["humidity_pct"],
                "wind_kmh": result["wind_kmh"],
                "as_of": result["as_of"],
            })
        else:
            await params.result_callback({
                "error": (
                    f"No weather data could be fetched for {location!r} right now — "
                    "this is a live-lookup failure, not a place that doesn't exist."
                ),
            })

        if serializer is not None and serializer.call_id:
            outcome = (
                f"{result['description']}, {result['temperature_c']:.1f}°C"
                if result else "no data found"
            )
            track_task(log_tool_call(
                serializer.call_id, "weather_lookup",
                f"weather in {location}", outcome, bool(result), exec_ms,
            ))

    return get_weather
