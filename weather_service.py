from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen


GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"


class WeatherServiceError(RuntimeError):
    pass


@dataclass
class GardenForecastDay:
    date: str
    label: str
    score_label: str
    score_variant: str
    max_temp: float | None
    min_temp: float | None
    rain_probability: int | None
    rain_amount: float | None
    wind_speed: float | None
    summary: str


def geocode_location(query: str) -> dict[str, str]:
    params = {
        "name": query.strip(),
        "count": 1,
        "language": "nl",
        "format": "json",
    }
    payload = _fetch_json(GEOCODE_URL, params)
    results = payload.get("results") or []
    if not results:
        raise WeatherServiceError("Ik kon deze tuinlocatie niet vinden. Probeer een duidelijkere plaatsnaam.")
    item = results[0]
    location_bits = [item.get("name"), item.get("admin1"), item.get("country")]
    return {
        "location_name": query.strip(),
        "location_label": ", ".join(part for part in location_bits if part),
        "latitude": str(item.get("latitude", "")),
        "longitude": str(item.get("longitude", "")),
        "timezone": str(item.get("timezone", "")),
    }


def fetch_garden_forecast(*, latitude: str, longitude: str, timezone: str = "auto") -> dict[str, object]:
    if not latitude or not longitude:
        raise WeatherServiceError("Sla eerst een tuinlocatie op om het tuinweer te zien.")
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "timezone": timezone or "auto",
        "forecast_days": 5,
        "daily": ",".join(
            [
                "weather_code",
                "temperature_2m_max",
                "temperature_2m_min",
                "precipitation_probability_max",
                "precipitation_sum",
                "wind_speed_10m_max",
            ]
        ),
    }
    payload = _fetch_json(FORECAST_URL, params)
    daily = payload.get("daily") or {}
    days = []
    dates = daily.get("time") or []
    for index, day in enumerate(dates):
        max_temp = _at(daily.get("temperature_2m_max"), index)
        min_temp = _at(daily.get("temperature_2m_min"), index)
        rain_probability = _at(daily.get("precipitation_probability_max"), index)
        rain_amount = _at(daily.get("precipitation_sum"), index)
        wind_speed = _at(daily.get("wind_speed_10m_max"), index)
        weather_code = _at(daily.get("weather_code"), index)
        score = _garden_day_score(
            max_temp=max_temp,
            min_temp=min_temp,
            rain_probability=rain_probability,
            rain_amount=rain_amount,
            wind_speed=wind_speed,
            weather_code=weather_code,
        )
        days.append(
            GardenForecastDay(
                date=day,
                label=_date_label(day),
                score_label=score["label"],
                score_variant=score["variant"],
                max_temp=max_temp,
                min_temp=min_temp,
                rain_probability=rain_probability,
                rain_amount=rain_amount,
                wind_speed=wind_speed,
                summary=score["summary"],
            )
        )

    best_day = next((day for day in days if day.score_variant == "good"), None) or (days[0] if days else None)
    return {"days": days, "best_day": best_day}


def _fetch_json(url: str, params: dict[str, object]) -> dict[str, object]:
    full_url = f"{url}?{urlencode(params)}"
    try:
        with urlopen(full_url, timeout=8) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:  # pragma: no cover
        raise WeatherServiceError("Het weerbericht is nu even niet bereikbaar.") from exc
    except URLError as exc:  # pragma: no cover
        raise WeatherServiceError("Het weerbericht kon niet worden geladen.") from exc


def _at(values: list[object] | None, index: int):
    if not values or index >= len(values):
        return None
    return values[index]


def _date_label(value: str) -> str:
    try:
        date = datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return value
    return date.strftime("%a %d %b").capitalize()


def _garden_day_score(
    *,
    max_temp: float | None,
    min_temp: float | None,
    rain_probability: int | None,
    rain_amount: float | None,
    wind_speed: float | None,
    weather_code: int | None,
) -> dict[str, str]:
    if min_temp is not None and min_temp <= 1:
        return {"label": "Beter overslaan", "variant": "skip", "summary": "Kans op koude of nachtvorst."}
    if rain_amount is not None and rain_amount >= 3:
        return {"label": "Beter overslaan", "variant": "skip", "summary": "Te nat voor fijn tuinwerk."}
    if rain_probability is not None and rain_probability >= 70:
        return {"label": "Beter overslaan", "variant": "skip", "summary": "Grote kans op regen."}
    if wind_speed is not None and wind_speed >= 30:
        return {"label": "Beter overslaan", "variant": "skip", "summary": "Er staat veel wind."}
    if weather_code in {0, 1, 2, 3} and (rain_probability or 0) < 35 and (wind_speed or 0) < 20:
        return {"label": "Goed tuinweer", "variant": "good", "summary": "Droog en prettig voor buitenwerk."}
    if max_temp is not None and max_temp >= 28:
        return {"label": "Twijfelachtig", "variant": "maybe", "summary": "Warm; beter kort en vroeg werken."}
    return {"label": "Twijfelachtig", "variant": "maybe", "summary": "Kan prima, maar check wind en regen nog even."}
