import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from skyfield.api import EarthSatellite, load, wgs84


TLE_URL = "https://celestrak.org/NORAD/elements/gp.php?GROUP=weather&FORMAT=tle"
# нужны М2, 2-2, 2-3, 2-4
METEOR_RE = re.compile(r"^METEOR-M\s*2(?:\D|$)", re.IGNORECASE)

# позиция
LATITUDE = float(input("Широта: "))
LONGITUDE = float(input("Долгота: "))
# на сколько смотрим
FORECAST_DAYS = 7

# необходимые параметры для наилучшей видимости
MIN_ELEVATION = 30.0
AZIMUTH_MIN = 60.0
AZIMUTH_MAX = 120.0
PARAMETER_DEVIATION = 10.0
BEST_PASS_MIN_ELEVATION = 20.0

TIMEZONE_NAME = "local"  # Например: "Asia/Novosibirsk"
JSON_PATH = Path(__file__).with_name("meteor_passes.json")


def get_timezone(name: str):
    if name == "local":
        return datetime.now().astimezone().tzinfo
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"неизвестный часовой пояс: {name}") from exc


def download_meteors(ts) -> list[EarthSatellite]:
    request = Request(TLE_URL, headers={"User-Agent": "meteor-pass-finder/1.0"})
    try:
        with urlopen(request, timeout=20) as response:
            lines = response.read().decode("ascii", errors="replace").splitlines()
    except URLError as exc:
        raise RuntimeError(f"не удалось скачать свежие TLE с CelesTrak: {exc}") from exc

    satellites = []
    for i in range(0, len(lines) - 2, 3):
        name, line1, line2 = (line.strip() for line in lines[i : i + 3])
        if METEOR_RE.search(name) and line1.startswith("1 ") and line2.startswith("2 "):
            satellites.append(EarthSatellite(line1, line2, name, ts))
    return satellites


def in_azimuth_sector(azimuth: float, start: float, end: float) -> bool:
    # проверка сектора
    azimuth, start, end = azimuth % 360, start % 360, end % 360
    return start <= azimuth <= end if start <= end else azimuth >= start or azimuth <= end


def angular_distance(first: float, second: float) -> float:
    # кратчайшее расстояние
    return abs((first - second + 180) % 360 - 180)


def distance_to_azimuth_sector(azimuth: float, start: float, end: float) -> float:
    if in_azimuth_sector(azimuth, start, end):
        return 0.0
    return min(angular_distance(azimuth, start), angular_distance(azimuth, end))


def angle_at(satellite, observer, moment) -> tuple[float, float]:
    altitude, azimuth, _ = (satellite - observer).at(moment).altaz()
    return altitude.degrees, azimuth.degrees


def save_json(rows: list, LATITUDE: float, LONGITUDE: float, tz) -> None:
    passes = []
    for _, aos_los_miss, negative_matches, total_miss, name, p in rows:
        passes.append(
            {
                "satellite": name,
                "match_type": "exact" if negative_matches == -2 else "close",
                "matched_parameters": -negative_matches,
                "total_miss_deg": round(total_miss, 3),
                "aos_los_score_deg": round(aos_los_miss, 3),
                "aos_utc": p["aos"].utc_datetime().isoformat(),
                "aos_local": p["aos"].utc_datetime().astimezone(tz).isoformat(),
                "aos_azimuth_deg": round(p["aos_az"], 3),
                "maximum_utc": p["maximum"].utc_datetime().isoformat(),
                "maximum_local": p["maximum"].utc_datetime().astimezone(tz).isoformat(),
                "max_elevation_deg": round(p["max_alt"], 3),
                "max_azimuth_deg": round(p["max_az"], 3),
                "los_utc": p["los"].utc_datetime().isoformat(),
                "los_local": p["los"].utc_datetime().astimezone(tz).isoformat(),
                "los_azimuth_deg": round(p["los_az"], 3),
            }
        )

    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "observer": {"latitude": LATITUDE, "longitude": LONGITUDE, "timezone": str(tz)},
        "settings": {
            "forecast_days": FORECAST_DAYS,
            "min_elevation_deg": MIN_ELEVATION,
            "azimuth_min_deg": AZIMUTH_MIN,
            "azimuth_max_deg": AZIMUTH_MAX,
            "parameter_deviation_deg": PARAMETER_DEVIATION,
            "best_pass_min_elevation_deg": BEST_PASS_MIN_ELEVATION,
        },
        "passes": passes,
    }
    JSON_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    try:
        tz = get_timezone(TIMEZONE_NAME)
        if FORECAST_DAYS <= 0 or not 0 <= MIN_ELEVATION < 90:
            raise ValueError("FORECAST_DAYS должен быть больше нуля, MIN_ELEVATION — от 0 до 90")
        if not 0 <= PARAMETER_DEVIATION < 90:
            raise ValueError("PARAMETER_DEVIATION должен быть от 0 до 90")
        if not 0 <= BEST_PASS_MIN_ELEVATION < 90:
            raise ValueError("BEST_PASS_MIN_ELEVATION должен быть от 0 до 90")

        ts = load.timescale()
        satellites = download_meteors(ts)
        if not satellites:
            raise RuntimeError("в наборе CelesTrak не найдено ни одного METEOR-M 2")

        observer = wgs84.latlon(LATITUDE, LONGITUDE)
        start_dt = datetime.now(timezone.utc)
        end_dt = start_dt + timedelta(days=FORECAST_DAYS)
        t0, t1 = ts.from_datetime(start_dt), ts.from_datetime(end_dt)
        rows = []

        for satellite in satellites:
            search_elevation = max(0.0, MIN_ELEVATION - PARAMETER_DEVIATION)
            times, events = satellite.find_events(observer, t0, t1, altitude_degrees=search_elevation)
            current = {}
            for moment, event in zip(times, events):
                altitude, azimuth = angle_at(satellite, observer, moment)
                if event == 0:  # подъём выше min-elevation (AOS для выбранного порога)
                    current = {"aos": moment, "aos_az": azimuth}
                elif event == 1 and current:
                    current.update(maximum=moment, max_alt=altitude, max_az=azimuth)
                elif event == 2 and "maximum" in current:
                    current.update(los=moment, los_az=azimuth)
                    elevation_shortfall = max(0.0, MIN_ELEVATION - current["max_alt"])
                    azimuth_miss = distance_to_azimuth_sector(
                        current["max_az"], AZIMUTH_MIN, AZIMUTH_MAX
                    )
                    if elevation_shortfall <= PARAMETER_DEVIATION and azimuth_miss <= PARAMETER_DEVIATION:
                        exact_matches = int(elevation_shortfall == 0) + int(azimuth_miss == 0)
                        total_miss = elevation_shortfall + azimuth_miss
                        if current["max_alt"] >= BEST_PASS_MIN_ELEVATION:
                            aos_los_miss = (
                                angular_distance(current["aos_az"], 0.0)
                                + angular_distance(current["los_az"], 180.0)
                            )
                            rows.append(
                                (
                                    current["aos"].utc_datetime(),
                                    aos_los_miss,
                                    -exact_matches,
                                    total_miss,
                                    satellite.name,
                                    current.copy(),
                                )
                            )
                    current = {}

        # Сначала ближайшая календарная дата. Внутри даты лучший пролёт — тот,
        # у которого AOS ближе к 0°, а LOS ближе к 180°.
        rows.sort(
            key=lambda item: (
                item[0].astimezone(tz).date(),
                item[1],
                item[0],
            )
        )
        save_json(rows, LATITUDE, LONGITUDE, tz)
        print(f"JSON сохранён: {JSON_PATH}")
        if not rows:
            print("Подходящих пролётов за выбранный период нет.")
            return 0
        return 0
    except (ValueError, RuntimeError) as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1


main()
