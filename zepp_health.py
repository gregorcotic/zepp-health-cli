#!/usr/bin/env python3
"""Minimal Zepp (Huami) API wrapper.

Reverse-engineered from HTTPS proxy captures of the official Zepp mobile app.
Not affiliated with Zepp Health. See README.md for setup and caveats.

Credentials are read from (highest priority first):
  1. --config <path>
  2. $ZEPP_CONFIG
  3. ./config.json
  4. ~/.config/zepp/config.json
  5. Individual env vars: ZEPP_APP_TOKEN, ZEPP_USER_ID, ZEPP_HOST, ...
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sqlite3
import sys
import time
import urllib.parse
import uuid
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests
from zepp_db import (
    FRESHNESS_TIMEZONE,
    Database,
    backup_database,
    inspect_database_file,
    resolve_db_path,
    restore_database,
)
from zepp_ops import SyncLock, lock_is_held

DEFAULT_HOST = "api-mifit-us3.zepp.com"

# Resolved at parse time from --config or $ZEPP_CONFIG; falls back to search list below.
_CONFIG_PATH_OVERRIDE: str | None = None

CONFIG_KEYS = (
    "app_token",
    "user_id",
    "host",
    "app_platform",
    "lang",
    "country",
    "timezone",
    "app_version",
    "user_agent",
    "cv",
    "vb",
    "db_path",
)
ENV_BY_KEY = {
    "app_token": "ZEPP_APP_TOKEN",
    "user_id": "ZEPP_USER_ID",
    "host": "ZEPP_HOST",
    "app_platform": "ZEPP_APP_PLATFORM",
    "lang": "ZEPP_LANG",
    "country": "ZEPP_COUNTRY",
    "timezone": "ZEPP_TIMEZONE",
    "app_version": "ZEPP_APP_VERSION",
    "user_agent": "ZEPP_USER_AGENT",
    "cv": "ZEPP_CV",
    "vb": "ZEPP_VB",
}


def _config_search_paths() -> list[Path]:
    paths: list[Path] = []
    if _CONFIG_PATH_OVERRIDE:
        paths.append(Path(_CONFIG_PATH_OVERRIDE).expanduser())
    env_path = os.environ.get("ZEPP_CONFIG", "").strip()
    if env_path:
        paths.append(Path(env_path).expanduser())
    paths.append(Path.cwd() / "config.json")
    paths.append(Path.home() / ".config" / "zepp" / "config.json")
    seen: set[Path] = set()
    out: list[Path] = []
    for p in paths:
        rp = p.resolve() if p.exists() else p
        if rp not in seen:
            seen.add(rp)
            out.append(p)
    return out


def load_config() -> dict[str, Any]:
    """Load config from JSON, with env vars overriding individual keys."""
    cfg: dict[str, Any] = {}
    for p in _config_search_paths():
        if p.is_file():
            try:
                cfg = json.loads(p.read_text())
                cfg["_loaded_from"] = str(p)
            except json.JSONDecodeError as exc:
                raise SystemExit(f"Invalid JSON in {p}: {exc}")
            break
    for key, env in ENV_BY_KEY.items():
        v = os.environ.get(env, "").strip()
        if v:
            cfg[key] = v
    return cfg


def save_config(data: dict[str, Any], path: Path | None = None) -> Path:
    target = path or (Path.cwd() / "config.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    clean = {k: v for k, v in data.items() if k in CONFIG_KEYS and v}
    target.write_text(json.dumps(clean, indent=2) + "\n")
    try:
        os.chmod(target, 0o600)
    except OSError:
        pass
    return target


def _r() -> str:
    return str(uuid.uuid4()).upper()


def _today_utc() -> date:
    return datetime.now(timezone.utc).date()


class ZeppClient:
    def __init__(
        self,
        apptoken: str,
        user_id: str,
        host: str = DEFAULT_HOST,
        app_platform: str = "ios_phone",
        timeout: float = 30.0,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.apptoken = apptoken
        self.user_id = user_id
        self.base = f"https://{host}"
        self.session = requests.Session()
        self.timeout = timeout
        self.session.headers.update(self._headers(app_platform, extra_headers or {}))

    def _headers(
        self, app_platform: str, extra: dict[str, str]
    ) -> dict[str, str]:
        defaults = {
            "apptoken": self.apptoken,
            "appname": "com.huami.midong",
            "appplatform": app_platform,
            "accept": "*/*",
            "accept-encoding": "gzip, deflate, br",
            "v": "2.0",
            "vn": "10.2.5",
            "cv": "1722_10.2.5",
            "vb": "202604132257",
            "user-agent": "Zepp/10.2.5 (iPhone; iOS 26.3.1; Scale/3.00)",
            "lang": "en",
            "country": "",
            "timezone": "UTC",
        }
        defaults.update({k: v for k, v in extra.items() if v})
        return defaults

    def get_json(self, path: str, params: dict[str, Any]) -> Any:
        q = {"r": _r(), **params}
        url = f"{self.base}{path}"
        r = self.session.get(url, params=q, timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def sport_load(
        self,
        start_day: date,
        end_day: date,
        *,
        limit: int = 900,
        next_cursor: int | None = None,
    ) -> Any:
        params: dict[str, Any] = {
            "startDay": start_day.isoformat(),
            "endDay": end_day.isoformat(),
            "limit": limit,
            "isReverse": "true",
        }
        if next_cursor is not None:
            params["next"] = next_cursor
        return self.get_json(
            f"/v2/watch/users/{self.user_id}/WatchSportStatistics/SPORT_LOAD",
            params,
        )

    def vo2_max(self, start_day: date, end_day: date) -> Any:
        return self.get_json(
            f"/v2/watch/users/{self.user_id}/WatchSportStatistics/VO2_MAX",
            {
                "startDay": start_day.isoformat(),
                "endDay": end_day.isoformat(),
                "limit": 900,
                "isReverse": "true",
            },
        )

    def heart_rate(
        self,
        start_ts: int,
        end_ts: int,
        *,
        limit: int = 1000,
        hr_type: int = 2,
    ) -> Any:
        return self.get_json(
            f"/users/{self.user_id}/heartRate",
            {
                "startTime": start_ts,
                "endTime": end_ts,
                "limit": limit,
                "type": hr_type,
            },
        )

    def weight_records(self, from_ts: int, to_ts: int, *, limit: int = 300) -> Any:
        return self.get_json(
            f"/users/{self.user_id}/members/-1/weightRecords",
            {
                "fromTime": from_ts,
                "toTime": to_ts,
                "limit": limit,
                "isForward": 0,
            },
        )

    def sport_history(
        self,
        sport: str,
        start_track_id: int,
        stop_track_id: int,
        *,
        need_sub_data: int = 1,
    ) -> Any:
        """Workout history. `sport` is the URL segment (e.g. run, walking, ride, swimming)."""
        return self.get_json(
            f"/v1/sport/{sport}/history.json",
            {
                "userid": self.user_id,
                "startTrackId": start_track_id,
                "stopTrackId": stop_track_id,
                "need_sub_data": need_sub_data,
                "type": "",
            },
        )

    def run_history(
        self,
        start_track_id: int,
        stop_track_id: int,
        *,
        need_sub_data: int = 1,
    ) -> Any:
        return self.sport_history(
            "run", start_track_id, stop_track_id, need_sub_data=need_sub_data
        )

    def sport_detail(self, track_id: str | int, source: str) -> Any:
        """Workout detail contract observed in public Zepp exporter code."""
        return self.get_json(
            "/v1/sport/run/detail.json",
            {
                "trackid": track_id,
                "source": source,
            },
        )

    def band_data(
        self,
        from_date: date,
        to_date: date,
        *,
        query_type: str = "detail",
        byte_length: int = 8,
        device_type: int = 0,
    ) -> Any:
        """Raw band sync bucket (sleep segments, steps, etc.) — `query_type` detail|summary."""
        return self.get_json(
            "/v1/data/band_data.json",
            {
                "userid": self.user_id,
                "from_date": from_date.isoformat(),
                "to_date": to_date.isoformat(),
                "query_type": query_type,
                "byteLength": byte_length,
                "device_type": device_type,
            },
        )

    def manual_data(self, manual_type: str) -> Any:
        """Manual entries (e.g. sleep sessions logged in app)."""
        return self.get_json(
            "/v1/user/manualData.json",
            {"userid": self.user_id, "type": manual_type},
        )

    def get_user_info(self) -> Any:
        return self.get_json(
            "/huami.health.getUserInfo.json",
            {"userid": self.user_id},
        )

    def blood_pressure_me(
        self,
        *,
        days: int = 7,
        to_date: date | None = None,
        source: str = "com.huami.midong.associated,com.huami.midong",
    ) -> Any:
        td = to_date or _today_utc()
        return self.get_json(
            "/users/me/bloodPressure",
            {
                "days": days,
                "sourceArrayStr": source,
                "toDate": td.isoformat(),
            },
        )

    def events_user(
        self,
        event_type: str,
        from_ms: int,
        to_ms: int,
        *,
        sub_type: str | None = None,
        limit: int = 2000,
        reverse: bool = False,
    ) -> Any:
        """User-scoped timeline (`/users/{id}/events`) — stress, PAI, SpO₂ clicks, etc."""
        params: dict[str, Any] = {
            "eventType": event_type,
            "from": from_ms,
            "to": to_ms,
            "limit": limit,
            "reverse": 1 if reverse else 0,
            "userId": self.user_id,
        }
        if sub_type:
            params["subType"] = sub_type
        return self.get_json(f"/users/{self.user_id}/events", params)

    def events_user_date_string(
        self,
        event_type: str,
        sub_type: str,
        from_iso: str,
        to_iso: str,
        *,
        tz: str,
        limit: int = 999,
        reverse: bool = False,
    ) -> Any:
        """Same as events_user but with ISO date bounds (used for SpO₂ ODI/OSA, etc.)."""
        return self.get_json(
            f"/users/{self.user_id}/events/dateString",
            {
                "eventType": event_type,
                "subType": sub_type,
                "from": from_iso,
                "to": to_iso,
                "timeZone": tz,
                "limit": limit,
                "reverse": 1 if reverse else 0,
                "userId": self.user_id,
            },
        )

    def file_info_events(
        self,
        event_type: str,
        sub_type: str,
        from_ms: int,
        to_ms: int,
        *,
        limit: int = 200,
    ) -> Any:
        """Per-second HR file index (`/users/me/fileInfo/events`)."""
        return self.get_json(
            "/users/me/fileInfo/events",
            {
                "eventType": event_type,
                "subType": sub_type,
                "from": from_ms,
                "to": to_ms,
                "limit": limit,
            },
        )

    def events(
        self,
        event_type: str,
        sub_type: str,
        from_ms: int,
        to_ms: int,
        *,
        limit: int = 200,
        reverse: bool = True,
    ) -> Any:
        """Generic /v2/users/me/events query (millisecond epochs)."""
        return self.get_json(
            "/v2/users/me/events",
            {
                "eventType": event_type,
                "subType": sub_type,
                "from": from_ms,
                "to": to_ms,
                "limit": limit,
                "reverse": 1 if reverse else 0,
            },
        )


def _load_client() -> ZeppClient:
    cfg = load_config()
    token = (cfg.get("app_token") or "").strip()
    uid = str(cfg.get("user_id") or "").strip()
    if not token or not uid:
        searched = "\n  ".join(str(p) for p in _config_search_paths())
        print(
            "Missing app_token or user_id.\n\n"
            "Set them by either:\n"
            "  1. Run:  python3 zepp_health.py init <proxy-capture>\n"
            "     (accepts an HTTPS proxy export: HAR, or the JSON session\n"
            "      format used by common HTTPS proxy/inspection tools)\n"
            "  2. Or create config.json (paths searched, in order):\n"
            f"     {searched}\n"
            '     {{ "app_token": "...", "user_id": "...", "host": "api-mifit-us3.zepp.com" }}\n'
            "  3. Or export env vars: ZEPP_APP_TOKEN, ZEPP_USER_ID, ZEPP_HOST",
            file=sys.stderr,
        )
        sys.exit(1)
    host = (cfg.get("host") or DEFAULT_HOST).strip()
    platform = (cfg.get("app_platform") or "ios_phone").strip()
    extra = {
        "lang": cfg.get("lang") or "",
        "country": cfg.get("country") or "",
        "timezone": cfg.get("timezone") or "",
        "vn": cfg.get("app_version") or "",
        "user-agent": cfg.get("user_agent") or "",
        "cv": cfg.get("cv") or "",
        "vb": cfg.get("vb") or "",
    }
    return ZeppClient(token, uid, host=host, app_platform=platform, extra_headers=extra)


_USER_ID_RE = __import__("re").compile(r"/users/(\d+)/")


def _normalize_entries(raw: Any) -> list[dict[str, Any]]:
    """Normalize a proxy capture into a list of {host, path, headers}.

    Supports two common JSON exports:
      - HAR (HTTP Archive):  {"log": {"entries": [{"request": {...}}, ...]}}
      - Generic proxy JSON session:  [ {"host": "...", "path": "...",
          "request": {"header": {"headers": [{"name": .., "value": ..}]}}}, ... ]
    """
    out: list[dict[str, Any]] = []
    if isinstance(raw, dict) and isinstance(raw.get("log"), dict):
        for e in raw["log"].get("entries") or []:
            req = e.get("request") or {}
            url = req.get("url") or ""
            try:
                parsed = urllib.parse.urlparse(url)
                host = parsed.hostname or ""
                path = parsed.path or ""
            except ValueError:
                host, path = "", ""
            headers = [
                {"name": h.get("name", ""), "value": h.get("value", "")}
                for h in (req.get("headers") or [])
            ]
            out.append({"host": host, "path": path, "headers": headers})
        return out
    if isinstance(raw, list):
        for e in raw:
            headers = (
                e.get("request", {}).get("header", {}).get("headers") or []
            )
            out.append(
                {
                    "host": e.get("host") or "",
                    "path": e.get("path") or "",
                    "headers": headers,
                }
            )
        return out
    raise SystemExit(
        "Unrecognized capture format. Expected HAR ({log:{entries:[...]}}) or "
        "a JSON array of session entries."
    )


def _extract_from_capture(path: Path) -> dict[str, Any]:
    """Pull app_token, user_id, and regional host from a proxy capture file."""
    raw = json.loads(path.read_text())
    entries = _normalize_entries(raw)

    token: str | None = None
    uid: str | None = None
    for e in entries:
        for h in e["headers"]:
            if (h.get("name") or "").lower() == "apptoken":
                token = token or h.get("value")
        m = _USER_ID_RE.search(e["path"])
        if m and not uid:
            uid = m.group(1)
        if token and uid:
            break

    with_token_hosts: list[str] = []
    for e in entries:
        host = e["host"]
        if not host or "zepp.com" not in host:
            continue
        if any((h.get("name") or "").lower() == "apptoken" for h in e["headers"]):
            with_token_hosts.append(host)
    seen: set[str] = set()
    ordered = [h for h in with_token_hosts if not (h in seen or seen.add(h))]
    host = next((h for h in ordered if "api-mifit" in h), None) or (
        ordered[0] if ordered else None
    )
    if not host:
        host = next(
            (e["host"] for e in entries if "zepp.com" in (e["host"] or "")),
            None,
        )

    if not (token and uid):
        raise SystemExit(
            f"Could not find apptoken / user id in {path}. "
            "Make sure the capture includes authenticated requests to api-mifit*.zepp.com."
        )
    return {"app_token": token, "user_id": uid, "host": host or DEFAULT_HOST}


def cmd_init(args: argparse.Namespace) -> None:
    src = Path(args.capture).expanduser()
    if not src.is_file():
        sys.exit(f"Capture file not found: {src}")
    extracted = _extract_from_capture(src)
    target = Path(args.output).expanduser() if args.output else (Path.cwd() / "config.json")
    if target.exists() and not args.force:
        existing = {}
        try:
            existing = json.loads(target.read_text())
        except json.JSONDecodeError:
            pass
        existing.update(extracted)
        merged = existing
    else:
        merged = extracted
    saved = save_config(merged, target)
    print(f"Wrote {saved} (mode 600)")
    print(f"  user_id: {merged.get('user_id')}")
    print(f"  host:    {merged.get('host')}")
    masked = (merged.get("app_token") or "")[:6] + "…" + (merged.get("app_token") or "")[-6:]
    print(f"  token:   {masked}  ({len(merged.get('app_token') or '')} chars)")


def cmd_config(args: argparse.Namespace) -> None:
    cfg = load_config()
    if args.show:
        out = dict(cfg)
        if out.get("app_token"):
            t = out["app_token"]
            out["app_token"] = t[:6] + "…" + t[-6:]
        print(json.dumps(out, indent=2))
        return
    if args.path:
        for p in _config_search_paths():
            print(p, "(exists)" if p.is_file() else "")
        return
    sys.exit("Use --show or --path")


def cmd_vo2(args: argparse.Namespace) -> None:
    c = _load_client()
    end = _today_utc()
    start = end - timedelta(days=args.days - 1)
    data = c.vo2_max(start, end)
    _emit_json(data, args)


def cmd_heart_rate(args: argparse.Namespace) -> None:
    c = _load_client()
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=args.days)
    data = c.heart_rate(int(start.timestamp()), int(end.timestamp()))
    _emit_json(data, args)


def cmd_weight(args: argparse.Namespace) -> None:
    c = _load_client()
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=args.days)
    data = c.weight_records(int(start.timestamp()), int(end.timestamp()))
    _emit_json(data, args)


def cmd_run_history(args: argparse.Namespace) -> None:
    c = _load_client()
    # Window around "today" in UTC — adjust if you track by local midnight.
    day = _today_utc()
    start_of_day = int(
        datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp()
    )
    sport = getattr(args, "sport", "run") or "run"
    data = c.sport_history(sport, start_of_day, start_of_day)
    _emit_json(data, args)


ACTIVITY_RECORD_WRAPPERS = (
    "items", "data", "summary", "records", "result", "trackList", "tracks",
    "sportData",
)
ACTIVITY_TEXT_FIELDS = {
    "name", "title", "description", "note", "notes", "remark", "comment",
    "memo", "workoutName", "sportName", "sport_title", "app_name",
    "crossfitContent", "coachInsight",
}
ACTIVITY_SAFE_SCALAR_HINTS = (
    "id", "sport", "type", "mode", "start", "end", "time", "date",
    "duration", "distance", "elevation", "altitude", "ascent", "descent",
    "climb", "heartrate", "heart_rate", "hr", "calorie", "speed", "pace",
    "cadence", "power", "load", "aerobic", "anaerobic", "training",
    "recovery", "vo2", "pai", "step", "stroke", "swolf", "lap", "split",
    "pool", "temperature", "moving", "elapsed", "epoc",
)
ACTIVITY_COACHING_FIELDS = (
    "rpe", "te", "anaerobic_te", "exercise_load", "workoutBalance",
    "strengthScores", "strength_training_group", "totalCardiacExertion",
    "totalMuscularExertion", "totalExertion", "totalInsight",
    "crossfitContent", "coachInsight",
)
ACTIVITY_NESTED_DETAIL_FIELDS = (
    "child_list", "add_info", "originSummary", "strengthScores",
    "workoutBalance", "location",
)
ACTIVITY_COORDINATE_KEYS = {
    "lat", "latitude", "lon", "lng", "longitude",
}
ACTIVITY_COVERAGE_FIELDS = (
    "trackid", "type", "sport_mode", "start_time", "end_time",
    "syncedTimezone", "run_time", "totalTimeWithMillis",
    "exerciseTimeWithMillis", "pause_time", "pauseTimeWithMillis",
    "dis", "highPrecisionDistance", "calorie", "avg_heart_rate",
    "max_heart_rate", "min_heart_rate", "exercise_load", "te",
    "anaerobic_te", "rpe", "VO2_max", "sport_title", "crossfitContent",
    "avg_pace", "max_pace", "min_pace", "avg_cadence", "max_cadence",
    "average_power", "max_power", "avg_stride_length", "total_step",
    "elevationGain", "elevationLoss", "altitude_ascend",
    "altitude_descend", "highestAltitude", "lowestAltitude",
    "averageAltitude", "max_altitude", "min_altitude", "avg_altitude",
    "distance_ascend", "climb_dis_descend", "totalClimbDistance",
    "cumulativeClimbingAscent", "maximumClimbingAscent", "swim_pool_length",
    "waterType", "lap_distance", "swim_style", "swolf", "total_strokes",
    "totalStrokes", "strokes", "avg_stroke_speed", "max_stroke_speed",
    "avg_distance_per_stroke", "freestyle_length", "breast_stroke_length",
    "back_stroke_length", "butterfly_length", "other_stroke_length",
    "medley_length", "strengthScores", "strength_training_group",
    "totalCardiacExertion", "totalMuscularExertion", "totalExertion",
    "workoutBalance", "totalWeightLoad", "total_group", "child_list",
    "add_info", "originSummary", "runningType", "runningProgram",
    "downhill_num", "durationOfDownhillWithMillis",
    "downhill_max_altitude_desend", "averageAirTemp", "highestAirTemp",
    "lowestAirTemp", "avg_pressure", "max_pressure", "min_pressure",
    "avg_slope", "max_slope", "spo2_min", "spo2_max",
    "maximumDepth", "divingAverageDepth", "averageMaxDepth",
    "numberOfDives", "averageDiveSpeed", "maximumDiveSpeed",
    "totalDiveTimeWithMillis", "avgDiveTimeWithMillis",
    "maxDiveTimeWithMillis", "totalSurfaceTimeWithMillis",
    "avgSurfaceTimeWithMillis", "supportedMaxDepth", "avg_temperature",
    "max_temperature", "min_temperature",
)
ACTIVITY_UNPROVEN_NEGATIVE_CANDIDATES = {-1, -100, -20000, -274}
ACTIVITY_SPORT_CATALOG = {
    (196, 0): ("Outdoor Free Diving", "Free Diving", False),
    (105, 0): ("Ski", "Ski", False),
    (130, 0): ("Cross-training", "Cross-training", False),
    (14, 0): ("Pool Swim", "Swimming", False),
    (15, 0): ("Open Water Swim", "Swimming", False),
    (15, 5): ("Open Water Swim - Zepp Coach", "Swimming", True),
    (207, 0): ("E-MTB", "Cycling", False),
    (208, 0): ("Gravel Cycling", "Cycling", False),
    (22, 0): ("Hiking", "Hiking", False),
    (22, 5): ("Hiking - Zepp Coach", "Hiking", True),
    (224, 0): ("Mountain Hiking", "Hiking", False),
    (6, 0): ("Walking", "Walking", False),
    (6, 5): ("Walking - Zepp Coach", "Walking", True),
    (9, 0): ("Outdoor Cycling", "Cycling", False),
    (9, 5): ("Outdoor Cycling - Zepp Coach", "Cycling", True),
}
ACTIVITY_CAPABILITY_FIXTURES = (
    ("Ski", 105, 0, "1767339463", "2026-01-02"),
    ("Cross-training", 130, 0, "1784739852", "2026-07-22"),
    ("Pool Swim", 14, 0, "1780212041", "2026-05-31"),
    ("Open Water Swim", 15, 0, "1783403679", "2026-07-07"),
    (
        "Open Water Swim - Zepp Coach", 15, 5, "1780763442",
        "2026-06-06",
    ),
    ("E-MTB", 207, 0, "1781713274", "2026-06-17"),
    ("Gravel Cycling", 208, 0, "1783747838", "2026-07-11"),
    ("Hiking", 22, 0, "1784948221", "2026-07-25"),
    ("Hiking - Zepp Coach", 22, 5, "1781345402", "2026-06-13"),
    ("Mountain Hiking", 224, 0, "1779616645", "2026-05-24"),
    ("Walking", 6, 0, "1784053037", "2026-07-14"),
    ("Walking - Zepp Coach", 6, 5, "1770024247", "2026-02-02"),
    ("Outdoor Cycling", 9, 0, "1784448489", "2026-07-19"),
    (
        "Outdoor Cycling - Zepp Coach", 9, 5, "1772025510",
        "2026-02-25",
    ),
)
ACTIVITY_SPORT_MAPPINGS = {
    key: {
        "type": key[0],
        "sport_name": name,
        "sport_family": family,
        "sport_mode": key[1],
        "zepp_coach_mode": coach_mode,
        "confidence": "PRODUCTION_PROVEN_MANUAL_APP_MATCH",
        "evidence": "Manually verified against a real Zepp app activity",
    }
    for key, (name, family, coach_mode) in ACTIVITY_SPORT_CATALOG.items()
}
ACTIVITY_PROVEN_METRIC_SEMANTICS = {
    (196, 0),
    (105, 0),
    (130, 0),
    (22, 0),
}
ACTIVITY_SPORT_SEMANTICS = {
    "Hiking": {
        "primary_metrics": (
            "distance", "duration", "elevation_gain", "elevation_loss",
            "altitude", "heart_rate", "training_load", "steps",
        ),
        "athlete_powered_ascent": True,
        "climbing_effort_relevant": True,
        "semantic_confidence": "INFERRED",
    },
    "Walking": {
        "primary_metrics": (
            "distance", "duration", "elevation_gain", "elevation_loss",
            "heart_rate", "training_load", "steps",
        ),
        "athlete_powered_ascent": True,
        "climbing_effort_relevant": True,
        "semantic_confidence": "INFERRED",
    },
    "Cycling": {
        "primary_metrics": (
            "distance", "duration", "elevation_gain", "elevation_loss",
            "heart_rate", "training_load", "cadence", "power",
        ),
        "athlete_powered_ascent": True,
        "climbing_effort_relevant": True,
        "semantic_confidence": "INFERRED",
    },
    "Swimming": {
        "primary_metrics": (
            "distance", "duration", "heart_rate", "training_load",
            "strokes", "swolf", "stroke_style", "pool_length",
        ),
        "athlete_powered_ascent": False,
        "climbing_effort_relevant": False,
        "semantic_confidence": "INFERRED",
    },
    "Cross-training": {
        "primary_metrics": (
            "duration", "heart_rate", "training_load", "exertion",
            "strength_detail",
        ),
        "athlete_powered_ascent": False,
        "climbing_effort_relevant": False,
        "semantic_confidence": "PROVEN",
    },
    "Ski": {
        "primary_metrics": (
            "distance", "duration", "vertical_descent", "descent_count",
            "heart_rate", "training_load", "altitude",
        ),
        "athlete_powered_ascent": False,
        "climbing_effort_relevant": False,
        "semantic_confidence": "PROVEN",
        "notes": (
            "altitude_descend is vertical descent; lift-assisted ascent is not "
            "athlete-powered climbing load"
        ),
    },
    "Free Diving": {
        "primary_metrics": (
            "duration", "depth", "dive_count", "diving_speed",
            "surface_recovery", "heart_rate", "temperature", "training_load",
        ),
        "athlete_powered_ascent": False,
        "climbing_effort_relevant": False,
        "semantic_confidence": "PROVEN",
        "notes": (
            "depth is positive distance below the water surface and is never "
            "altitude, elevation, or ski vertical"
        ),
    },
}


def _activity_records(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if not isinstance(data, dict):
        return []
    for key in ACTIVITY_RECORD_WRAPPERS:
        value = data.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        if isinstance(value, dict):
            nested = _activity_records(value)
            if nested:
                return nested
    return []


def _activity_shape(value: Any, *, depth: int = 0) -> dict[str, Any]:
    """Describe payload structure without emitting nested health/GPS values."""
    if isinstance(value, list):
        dictionaries = [item for item in value if isinstance(item, dict)]
        shape: dict[str, Any] = {
            "type": "list",
            "count": len(value),
        }
        if dictionaries:
            shape["sample_field_names"] = sorted(
                {str(key) for item in dictionaries[:3] for key in item}
            )
            if depth < 2:
                shape["sample_structure"] = _activity_shape(
                    dictionaries[0], depth=depth + 1
                )
        return shape
    if isinstance(value, dict):
        shape = {
            "type": "object",
            "field_names": sorted(str(key) for key in value),
        }
        if depth < 2:
            nested = {
                str(key): _activity_shape(item, depth=depth + 1)
                for key, item in value.items()
                if isinstance(item, (dict, list))
            }
            if nested:
                shape["nested"] = nested
        return shape
    return {"type": type(value).__name__}


def _activity_sensitive_key(key: str) -> bool:
    lower_key = key.lower()
    return any(
        marker in lower_key
        for marker in (
            "token", "authorization", "cookie", "userid", "user_id",
            "deviceid", "device_id", "devicesn", "device_sn", "secret",
            "downloadurl", "fileurl", "download_url", "file_url",
            "accountid", "account_id", "ownerid", "owner_id", "memberid",
            "member_id", "profileid", "profile_id", "url",
        )
    ) or lower_key in {"uid"}


def _activity_text_value(value: Any, *, include_text: bool) -> Any:
    if include_text:
        return value
    return {
        "present": value not in (None, ""),
        "length": len(str(value)) if value not in (None, "") else 0,
        "type": type(value).__name__,
    }


def _activity_safe_nested(
    value: Any, *, include_text: bool, depth: int = 0
) -> dict[str, Any]:
    """Expose bounded nested coaching structure without coordinates or identifiers."""
    if isinstance(value, list):
        result: dict[str, Any] = {"type": "list", "count": len(value)}
        if depth < 2:
            result["samples"] = [
                _activity_safe_nested(item, include_text=include_text, depth=depth + 1)
                for item in value[:3]
            ]
        return result
    if isinstance(value, dict):
        scalar_values: dict[str, Any] = {}
        text_values: dict[str, Any] = {}
        nested: dict[str, Any] = {}
        omitted_fields: list[str] = []
        for raw_key, item in list(value.items())[:50]:
            key = str(raw_key)
            lower_key = key.lower()
            if _activity_sensitive_key(key) or lower_key in ACTIVITY_COORDINATE_KEYS:
                omitted_fields.append(key)
            elif isinstance(item, (dict, list)):
                if depth < 2:
                    nested[key] = _activity_safe_nested(
                        item, include_text=include_text, depth=depth + 1
                    )
            elif isinstance(item, str):
                text_values[key] = _activity_text_value(
                    item, include_text=include_text
                )
            elif isinstance(item, (int, float, bool)) or item is None:
                scalar_values[key] = item
        result = {
            "type": "object",
            "field_names": sorted(str(key) for key in value),
            "scalar_values": scalar_values,
            "text_values": text_values,
        }
        if nested:
            result["nested"] = nested
        if omitted_fields:
            result["omitted_field_names"] = sorted(omitted_fields)
        return result
    if isinstance(value, str):
        return {
            "type": "str",
            "text": _activity_text_value(value, include_text=include_text),
        }
    return {"type": type(value).__name__, "value": value}


def _activity_gps_track_evidence(record: dict[str, Any]) -> dict[str, Any]:
    point_count = 0
    altitude_sample_count = 0
    hr_sample_count = 0
    track_fields: set[str] = set()
    timestamp_fields: set[str] = set()
    timestamp_values: list[int | float | str] = []

    def inspect(value: Any, *, parent_key: str = "", depth: int = 0) -> None:
        nonlocal point_count, altitude_sample_count, hr_sample_count
        if depth > 4 or parent_key.lower() == "location":
            return
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    lower_fields = {str(key).lower() for key in item}
                    has_lat = bool(lower_fields & {"lat", "latitude"})
                    has_lon = bool(lower_fields & {"lon", "lng", "longitude"})
                    if has_lat and has_lon:
                        point_count += 1
                        track_fields.update(str(key) for key in item)
                        if lower_fields & {"altitude", "alt", "elevation"}:
                            altitude_sample_count += 1
                        if lower_fields & {
                            "hr", "heart_rate", "heartrate", "heart-rate",
                        }:
                            hr_sample_count += 1
                        for raw_key, raw_value in item.items():
                            lower_key = str(raw_key).lower()
                            if lower_key in {
                                "timestamp", "time", "ts", "datetime", "date_time",
                            } and isinstance(raw_value, (int, float, str)):
                                timestamp_fields.add(str(raw_key))
                                timestamp_values.append(raw_value)
                    else:
                        inspect(item, parent_key=parent_key, depth=depth + 1)
        elif isinstance(value, dict):
            for key, item in value.items():
                inspect(item, parent_key=str(key), depth=depth + 1)

    inspect(record)
    time_coverage: dict[str, Any] = {
        "timestamp_field_names": sorted(timestamp_fields),
        "sample_count_with_timestamp": len(timestamp_values),
    }
    if timestamp_values and all(
        isinstance(value, (int, float)) for value in timestamp_values
    ):
        time_coverage["raw_start"] = min(timestamp_values)
        time_coverage["raw_end"] = max(timestamp_values)
    elif timestamp_values and all(isinstance(value, str) for value in timestamp_values):
        time_coverage["raw_start"] = min(timestamp_values)
        time_coverage["raw_end"] = max(timestamp_values)
    return {
        "gps_track_present": point_count > 0,
        "gps_point_count": point_count,
        "track_field_names": sorted(track_fields),
        "track_time_coverage": time_coverage,
        "altitude_stream_present": altitude_sample_count > 0,
        "altitude_sample_count": altitude_sample_count,
        "workout_hr_stream_present": hr_sample_count > 0,
        "workout_hr_sample_count": hr_sample_count,
    }


def _activity_number(value: Any) -> int | float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        try:
            number = float(value)
        except ValueError:
            return None
        if not math.isfinite(number):
            return None
        return int(number) if number.is_integer() else number
    return None


def _activity_is_unavailable_sentinel(value: Any) -> bool:
    number = _activity_number(value)
    return number in ACTIVITY_UNPROVEN_NEGATIVE_CANDIDATES


def _activity_usable_metric_number(value: Any) -> int | float | None:
    if _activity_is_unavailable_sentinel(value):
        return None
    return _activity_number(value)


def _activity_duration_seconds(
    record: dict[str, Any],
) -> tuple[int | float | None, str | None]:
    """Resolve duration using the established run/total/exercise precedence."""
    for field, divisor in (
        ("run_time", 1),
        ("totalTimeWithMillis", 1000),
        ("exerciseTimeWithMillis", 1000),
    ):
        value = _activity_usable_metric_number(record.get(field))
        if value is not None:
            return value if divisor == 1 else value / divisor, field
    return None, None


def _activity_distance_metres(
    record: dict[str, Any],
) -> tuple[int | float | None, str | None]:
    """Resolve distance using the established high-precision/dis precedence."""
    if is_alpine_ski_activity(record):
        ski_distance = _activity_usable_metric_number(
            record.get("climb_dis_descend")
        )
        if ski_distance is not None:
            return ski_distance, "climb_dis_descend"
    for field in ("highPrecisionDistance", "dis"):
        value = _activity_usable_metric_number(record.get(field))
        if value is not None:
            return value, field
    return None, None


def _activity_sport_mapping(record: dict[str, Any]) -> dict[str, Any] | None:
    type_value = _activity_number(record.get("type"))
    mode_value = _activity_number(record.get("sport_mode"))
    if not isinstance(type_value, int) or not isinstance(mode_value, int):
        return None
    return ACTIVITY_SPORT_MAPPINGS.get((type_value, mode_value))


def is_alpine_ski_activity(record: dict[str, Any]) -> bool:
    """Return whether an activity is the proven Alpine Ski pair (105, 0)."""
    return (
        _activity_number(record.get("type")) == 105
        and _activity_number(record.get("sport_mode")) == 0
    )


def interpret_activity_metrics(record: dict[str, Any]) -> dict[str, Any]:
    """Apply centralized sport semantics while retaining raw source metrics."""
    mapping = _activity_sport_mapping(record)
    raw_fields = (
        "run_time", "totalTimeWithMillis", "exerciseTimeWithMillis",
        "dis", "highPrecisionDistance", "calorie",
        "altitude_ascend", "altitude_descend", "elevationGain",
        "elevationLoss", "cumulativeClimbingAscent",
        "maximumClimbingAscent", "downhill_num",
        "downhill_max_altitude_desend", "climb_dis_descend",
        "max_altitude", "min_altitude", "avg_altitude",
    )
    raw_metrics = {
        field: record[field] for field in raw_fields if field in record
    }
    if mapping is None:
        return {
            "sport_mapping": None,
            "metric_semantics": "UNKNOWN",
            "semantic_profile": None,
            "raw_metrics": raw_metrics,
            "normalized_metrics": {},
            "climbing_load": {
                "athlete_powered_ascent_m": None,
                "eligible": False,
                "reason": "unknown_sport_semantics",
            },
        }

    profile = ACTIVITY_SPORT_SEMANTICS[mapping["sport_family"]]
    mapping_key = (mapping["type"], mapping["sport_mode"])
    semantic_confidence = (
        "PROVEN"
        if mapping_key in ACTIVITY_PROVEN_METRIC_SEMANTICS
        else profile["semantic_confidence"]
    )
    normalized: dict[str, Any] = {}
    duration, duration_field = _activity_duration_seconds(record)
    distance, distance_field = _activity_distance_metres(record)
    calories = _activity_usable_metric_number(record.get("calorie"))
    normalized.update({
        "duration_s": {
            "value": duration,
            "source_field": duration_field,
            "semantic_confidence": "PROVEN" if duration is not None else "UNKNOWN",
        },
        "distance_m": {
            "value": distance,
            "source_field": distance_field if distance is not None else None,
            "semantic_confidence": "PROVEN" if distance is not None else "UNKNOWN",
        },
        "calories_kcal": {
            "value": calories,
            "source_field": "calorie" if calories is not None else None,
            "semantic_confidence": "PROVEN" if calories is not None else "UNKNOWN",
        },
    })
    climbing_ascent = None
    ascent_metric_invalid = False
    ascent_metric_missing = False
    if is_alpine_ski_activity(record):
        descent = _activity_usable_metric_number(record.get("altitude_descend"))
        normalized["vertical_descent_m"] = {
            "value": descent,
            "source_field": "altitude_descend" if descent is not None else None,
            "semantic_confidence": "PROVEN" if descent is not None else "UNKNOWN",
            "evidence": (
                "Production Ski fixture trackid 1767339463: API "
                "altitude_descend=5921; Zepp app displayed approximately 5913 m"
                if descent is not None
                else "No altitude_descend value"
            ),
        }
        normalized["elevation_loss_m"] = dict(normalized["vertical_descent_m"])
        normalized["ski_vertical_m"] = dict(normalized["vertical_descent_m"])
        ski_ascent = _activity_usable_metric_number(record.get("altitude_ascend"))
        normalized["elevation_gain_m"] = {
            "value": ski_ascent,
            "source_field": "altitude_ascend" if ski_ascent is not None else None,
            "semantic_confidence": "PROVEN" if ski_ascent is not None else "UNKNOWN",
            "reason": "ski_lift_ascent_is_not_athlete_powered_climbing",
        }
    elif profile["athlete_powered_ascent"]:
        raw_ascent = record.get("altitude_ascend")
        ascent_metric_invalid = _activity_is_unavailable_sentinel(raw_ascent)
        ascent = _activity_usable_metric_number(raw_ascent)
        ascent_metric_missing = ascent is None and not ascent_metric_invalid
        descent = _activity_usable_metric_number(record.get("altitude_descend"))
        normalized["elevation_gain_m"] = {
            "value": ascent,
            "source_field": (
                "altitude_ascend"
                if ascent is not None or ascent_metric_invalid
                else None
            ),
            "semantic_confidence": (
                semantic_confidence if ascent is not None else "UNKNOWN"
            ),
        }
        if ascent_metric_invalid:
            normalized["elevation_gain_m"]["reason"] = (
                "invalid_or_unavailable_ascent_metric"
            )
        elif ascent_metric_missing:
            normalized["elevation_gain_m"]["reason"] = (
                "missing_or_unavailable_ascent_metric"
            )
        normalized["elevation_loss_m"] = {
            "value": descent,
            "source_field": "altitude_descend" if descent is not None else None,
            "semantic_confidence": (
                semantic_confidence if descent is not None else "UNKNOWN"
            ),
        }
        climbing_ascent = ascent

    return {
        "sport_mapping": mapping,
        "metric_semantics": semantic_confidence,
        "semantic_profile": profile,
        "raw_metrics": raw_metrics,
        "normalized_metrics": normalized,
        "climbing_load": {
            "athlete_powered_ascent_m": climbing_ascent,
            "eligible": (
                profile["climbing_effort_relevant"]
                and semantic_confidence == "PROVEN"
                and climbing_ascent is not None
            ),
            "reason": (
                "invalid_or_unavailable_ascent_metric"
                if ascent_metric_invalid
                else (
                    "missing_or_unavailable_ascent_metric"
                    if ascent_metric_missing
                    else (
                        "sport_semantics_classify_ascent_as_athlete_powered"
                        if (
                            profile["climbing_effort_relevant"]
                            and semantic_confidence == "PROVEN"
                            and climbing_ascent is not None
                        )
                        else (
                            "sport_metric_semantics_not_proven"
                            if profile["climbing_effort_relevant"]
                            else "sport_semantics_exclude_climbing_load"
                        )
                    )
                )
            ),
        },
    }


def _activity_summary(
    record: dict[str, Any], *, include_text: bool = False
) -> dict[str, Any]:
    scalars: dict[str, Any] = {}
    text: dict[str, Any] = {}
    nested: dict[str, Any] = {}
    coaching_fields: dict[str, Any] = {}
    location_metadata: dict[str, Any] = {"present": False}
    omitted_sensitive: list[str] = []
    unknown_scalar_fields: list[str] = []
    text_field_names = {item.lower() for item in ACTIVITY_TEXT_FIELDS}
    coaching_field_names = {item.lower() for item in ACTIVITY_COACHING_FIELDS}
    detail_field_names = {item.lower() for item in ACTIVITY_NESTED_DETAIL_FIELDS}
    for raw_key, value in record.items():
        key = str(raw_key)
        lower_key = key.lower()
        if _activity_sensitive_key(key):
            omitted_sensitive.append(key)
            continue
        if lower_key == "location":
            location_metadata = {
                "present": value not in (None, "", [], {}),
                "structure": _activity_safe_nested(
                    value, include_text=False
                ),
            }
            nested[key] = location_metadata["structure"]
            continue
        if lower_key in coaching_field_names:
            if isinstance(value, (dict, list)):
                coaching_fields[key] = _activity_safe_nested(
                    value, include_text=include_text
                )
            elif isinstance(value, str):
                coaching_fields[key] = _activity_text_value(
                    value, include_text=include_text
                )
            else:
                coaching_fields[key] = value
            if lower_key in text_field_names:
                text[key] = _activity_text_value(
                    value, include_text=include_text
                )
            continue
        if lower_key in text_field_names:
            text[key] = _activity_text_value(
                value, include_text=include_text
            )
        elif isinstance(value, (dict, list)):
            nested[key] = (
                _activity_safe_nested(value, include_text=include_text)
                if lower_key in detail_field_names
                else _activity_shape(value)
            )
        elif isinstance(value, (str, int, float, bool)) or value is None:
            if any(hint in lower_key for hint in ACTIVITY_SAFE_SCALAR_HINTS):
                scalars[key] = value
            else:
                unknown_scalar_fields.append(key)
    gps = _activity_gps_track_evidence(record)
    return {
        "field_names": sorted(str(key) for key in record),
        "scalar_fields": scalars,
        "coaching_fields": coaching_fields,
        "text_fields": text,
        "nested_structures": nested,
        "location_metadata": location_metadata,
        "gps_present": gps["gps_track_present"],
        **gps,
        "omitted_sensitive_field_names": sorted(omitted_sensitive),
        "unknown_scalar_field_names": sorted(unknown_scalar_fields),
        "semantic_interpretation": interpret_activity_metrics(record),
    }


def diagnose_activity_payload(
    data: Any,
    *,
    sport_segment: str,
    limit: int = 20,
    include_text: bool = False,
    track_id: str | int | None = None,
) -> dict[str, Any]:
    if limit < 1:
        raise ValueError("activity diagnostic limit must be at least 1")
    records = _activity_records(data)
    selected_records = records
    if track_id is not None:
        wanted = str(track_id)
        selected_records = [
            record
            for record in records
            if str(
                record.get(
                    "trackid",
                    record.get("trackId", record.get("track_id", "")),
                )
            ) == wanted
        ]
    response_metadata: dict[str, Any] = {}
    if isinstance(data, dict):
        if "code" in data and (
            isinstance(data.get("code"), (int, float, bool)) or data.get("code") is None
        ):
            response_metadata["code"] = data.get("code")
        response_data = data.get("data")
        if isinstance(response_data, dict):
            if "summary" in response_data and isinstance(response_data["summary"], list):
                response_metadata["record_wrapper"] = "data.summary"
            if "next" in response_data:
                next_cursor = response_data.get("next")
                if isinstance(next_cursor, (int, float, bool)) or next_cursor is None:
                    response_metadata["next"] = next_cursor
    return {
        "sport_segment": sport_segment,
        "raw_record_count": len(records),
        "track_id_filter": str(track_id) if track_id is not None else None,
        "matched_record_count": len(selected_records),
        "reported_record_count": min(len(selected_records), limit),
        "response_metadata": response_metadata,
        "response_structure": _activity_shape(data),
        "records": [
            _activity_summary(record, include_text=include_text)
            for record in selected_records[:limit]
        ],
    }


def _activity_field_status(record: dict[str, Any], field: str) -> str:
    if field not in record:
        return "ABSENT"
    value = record[field]
    if value is None or value == "" or value == [] or value == {}:
        return "PRESENT_EMPTY"
    if _activity_is_unavailable_sentinel(value):
        return "UNKNOWN_SEMANTICS"
    return "PRESENT_WITH_VALUE"


def _activity_response_next(data: Any) -> Any:
    if isinstance(data, dict) and isinstance(data.get("data"), dict):
        return data["data"].get("next")
    return None


def _activity_local_end_parts(
    value: Any, timezone_name: str
) -> tuple[str | None, str | None]:
    """Resolve a plausible epoch end_time without relabeling it as a start."""
    try:
        epoch = float(value)
    except (TypeError, ValueError):
        return None, None
    if epoch >= 1_000_000_000_000:
        epoch /= 1000
    try:
        local = datetime.fromtimestamp(epoch, ZoneInfo(timezone_name))
    except (OSError, OverflowError, ValueError, KeyError):
        return None, None
    if not 2000 <= local.year <= 2100:
        return None, None
    return local.date().isoformat(), local.strftime("%H:%M:%S")


def _activity_representative_metrics(
    record: dict[str, Any], timezone_name: str
) -> dict[str, Any]:
    end_time = record.get("end_time")
    local_date, local_time = _activity_local_end_parts(end_time, timezone_name)

    duration, duration_field = _activity_duration_seconds(record)

    distance, distance_field = _activity_distance_metres(record)

    calories = _activity_usable_metric_number(record.get("calorie"))
    return {
        "representative_end_time": end_time,
        "representative_local_date": local_date,
        "representative_local_time": local_time,
        "representative_duration": duration,
        "representative_duration_unit": "seconds" if duration is not None else None,
        "representative_duration_source_field": duration_field,
        "representative_distance": distance,
        "representative_distance_unit": "metres" if distance is not None else None,
        "representative_distance_source_field": distance_field,
        "representative_calories": calories,
        "representative_calories_unit": "kcal" if calories is not None else None,
        "representative_calories_source_field": (
            "calorie" if calories is not None else None
        ),
    }


def inventory_activity_payload(
    data: Any,
    *,
    sport_segment: str = "run",
    timezone_name: str = FRESHNESS_TIMEZONE,
) -> dict[str, Any]:
    """Group one bounded history response without claiming complete pagination."""
    records = _activity_records(data)
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for record in records:
        key = (str(record.get("type")), str(record.get("sport_mode")))
        groups.setdefault(key, []).append(record)

    type_groups: list[dict[str, Any]] = []
    for (type_value, sport_mode), members in sorted(groups.items()):
        representative = max(
            members,
            key=lambda item: int(item.get("trackid", 0))
            if str(item.get("trackid", "")).isdigit()
            else 0,
        )
        statuses: dict[str, dict[str, int]] = {}
        for field in ACTIVITY_COVERAGE_FIELDS:
            counts = Counter(_activity_field_status(item, field) for item in members)
            statuses[field] = dict(sorted(counts.items()))
        gps_evidence = [_activity_gps_track_evidence(item) for item in members]
        try:
            numeric_type = int(type_value)
            numeric_mode = int(sport_mode)
        except (TypeError, ValueError):
            numeric_type = None
            numeric_mode = None
        type_groups.append({
            "type": representative.get("type"),
            "sport_mode": representative.get("sport_mode"),
            "record_count": len(members),
            "representative_trackid": representative.get("trackid"),
            **_activity_representative_metrics(representative, timezone_name),
            "known_mapping": ACTIVITY_SPORT_MAPPINGS.get(
                (numeric_type, numeric_mode)
            ),
            "metric_semantics": (
                ACTIVITY_SPORT_SEMANTICS[
                    ACTIVITY_SPORT_MAPPINGS[(numeric_type, numeric_mode)][
                        "sport_family"
                    ]
                ]
                if (numeric_type, numeric_mode) in ACTIVITY_SPORT_MAPPINGS
                else None
            ),
            "representative_semantic_interpretation": interpret_activity_metrics(
                representative
            ),
            "field_status_counts": statuses,
            "location_metadata_present_count": sum(
                item.get("location") not in (None, "", [], {}) for item in members
            ),
            "gps_track_present_count": sum(
                item["gps_track_present"] for item in gps_evidence
            ),
            "altitude_stream_present_count": sum(
                item["altitude_stream_present"] for item in gps_evidence
            ),
            "workout_hr_stream_present_count": sum(
                item["workout_hr_stream_present"] for item in gps_evidence
            ),
        })

    next_cursor = _activity_response_next(data)
    if next_cursor == -1:
        page_status = "SINGLE_PAGE_TERMINAL_OBSERVED"
    elif next_cursor is None:
        page_status = "CURSOR_NOT_PRESENT"
    else:
        page_status = "INCOMPLETE_PAGINATION_UNRESOLVED"
    return {
        "sport_segment": sport_segment,
        "raw_record_count": len(records),
        "type_group_count": len(type_groups),
        "observed_type_ids": sorted(
            {item.get("type") for item in records},
            key=lambda value: str(value),
        ),
        "pagination": {
            "next": next_cursor,
            "status": page_status,
            "followed": False,
            "counts_are_complete_for_requested_window": next_cursor == -1,
            "note": (
                "No cursor is followed because data.next continuation semantics "
                "are not proven."
            ),
        },
        "type_groups": type_groups,
    }


def _activity_audit_field(record: dict[str, Any], field: str) -> dict[str, Any]:
    status = _activity_field_status(record, field)
    detail: dict[str, Any] = {"status": status}
    if field not in record:
        return detail
    value = record[field]
    if isinstance(value, (dict, list)):
        detail["structure"] = _activity_safe_nested(value, include_text=False)
    elif isinstance(value, str):
        number = _activity_number(value)
        if number is not None:
            detail["raw_value"] = number
            detail["raw_type"] = "str"
        elif field == "syncedTimezone":
            detail["raw_value"] = value
            detail["raw_type"] = "str"
        else:
            detail["text"] = _activity_text_value(value, include_text=False)
    elif isinstance(value, (int, float, bool)) or value is None:
        detail["raw_value"] = value
        detail["raw_type"] = type(value).__name__
    return detail


def _activity_gps_capability(
    record: dict[str, Any], mapping: dict[str, Any]
) -> dict[str, Any]:
    evidence = _activity_gps_track_evidence(record)
    location_present = record.get("location") not in (None, "", [], {})
    if mapping["sport_name"] in {"Pool Swim", "Cross-training"}:
        expectation = "GPS_NOT_APPLICABLE"
    elif mapping["sport_name"].startswith("Open Water Swim"):
        expectation = "GPS_EXPECTED"
    else:
        expectation = "GPS_USEFUL"
    if evidence["gps_track_present"]:
        indication = "GPS_INDICATED"
        track_status = "RAW_TRACK_AVAILABLE"
    elif expectation == "GPS_NOT_APPLICABLE":
        indication = "GPS_NOT_APPLICABLE"
        track_status = "GPS_NOT_APPLICABLE"
    else:
        indication = "GPS_INDICATED" if location_present else "GPS_NOT_INDICATED"
        track_status = "RAW_TRACK_NOT_FOUND"
    return {
        "expectation": expectation,
        "summary_location_metadata_present": location_present,
        "gps_indication": indication,
        "raw_track_status": track_status,
        **evidence,
    }


def _activity_sensor_evidence(
    field_details: dict[str, dict[str, Any]], fields: tuple[str, ...]
) -> str:
    populated = [
        field_details[field]
        for field in fields
        if field in field_details
        and field_details[field]["status"] == "PRESENT_WITH_VALUE"
    ]
    if any(
        isinstance(detail.get("raw_value"), (int, float))
        and not isinstance(detail.get("raw_value"), bool)
        and detail["raw_value"] > 0
        for detail in populated
    ):
        return "ACTIVITY_SENSOR_DATA_PRESENT"
    numeric_values = [
        detail["raw_value"]
        for detail in populated
        if isinstance(detail.get("raw_value"), (int, float))
        and not isinstance(detail.get("raw_value"), bool)
    ]
    if (
        populated
        and len(numeric_values) == len(populated)
        and all(value == 0 for value in numeric_values)
    ):
        return "ACTIVITY_ZERO_VALUE_SEMANTICS_UNKNOWN"
    if populated:
        return "ACTIVITY_SENSOR_VALUE_SEMANTICS_UNKNOWN"
    return "SPORT_CAPABILITY_UNKNOWN_ACTIVITY_HAS_NO_SENSOR_DATA"


def audit_activity_capabilities(
    data: Any, *, timezone_name: str = FRESHNESS_TIMEZONE
) -> dict[str, Any]:
    """Audit only the 14 approved representative activities."""
    records = _activity_records(data)
    by_track_id = {
        str(record.get("trackid", record.get("trackId", ""))): record
        for record in records
    }
    activities: list[dict[str, Any]] = []
    by_pair: dict[tuple[int, int], dict[str, Any]] = {}
    text_fields = (
        "sport_title", "crossfitContent", "coachInsight",
        "description", "notes", "remark", "memo",
    )
    for sport_name, type_id, sport_mode, track_id, fixture_date in (
        ACTIVITY_CAPABILITY_FIXTURES
    ):
        record = by_track_id.get(track_id)
        mapping = ACTIVITY_SPORT_MAPPINGS[(type_id, sport_mode)]
        if record is None:
            activities.append({
                "sport_name": sport_name,
                "type": type_id,
                "sport_mode": sport_mode,
                "fixture_date": fixture_date,
                "representative_trackid": track_id,
                "matched": False,
            })
            continue
        actual_pair = (
            _activity_number(record.get("type")),
            _activity_number(record.get("sport_mode")),
        )
        field_details = {
            field: _activity_audit_field(record, field)
            for field in ACTIVITY_COVERAGE_FIELDS
        }
        field_statuses = {
            field: detail["status"] for field, detail in field_details.items()
        }
        text_metadata = {
            field: _activity_audit_field(record, field)
            for field in text_fields
        }
        metric_status_counts = dict(Counter(field_statuses.values()))
        activity = {
            "sport_name": sport_name,
            "sport_family": mapping["sport_family"],
            "type": type_id,
            "sport_mode": sport_mode,
            "zepp_coach_mode": mapping["zepp_coach_mode"],
            "fixture_date": fixture_date,
            "representative_trackid": track_id,
            "matched": True,
            "identity_matches_expected_pair": actual_pair == (type_id, sport_mode),
            **_activity_representative_metrics(record, timezone_name),
            "metric_status_counts": metric_status_counts,
            "fields": field_details,
            "semantic_interpretation": interpret_activity_metrics(record),
            "gps": _activity_gps_capability(record, mapping),
            "cycling_sensor_evidence": {
                "cadence": _activity_sensor_evidence(
                    field_details, ("avg_cadence", "max_cadence")
                ),
                "power": _activity_sensor_evidence(
                    field_details, ("average_power", "max_power")
                ),
            } if mapping["sport_family"] == "Cycling" else None,
            "text_metadata": text_metadata,
            "workout_notes_status": (
                "APP_DATA_KNOWN_TO_EXIST_API_LOCATION_NOT_YET_DISCOVERED"
                if sport_name == "Cross-training"
                else "API_LOCATION_NOT_YET_DISCOVERED"
            ),
        }
        activities.append(activity)
        by_pair[(type_id, sport_mode)] = activity

    comparisons: list[dict[str, Any]] = []
    for type_id, normal_mode, coach_mode in (
        (6, 0, 5), (9, 0, 5), (15, 0, 5), (22, 0, 5)
    ):
        normal = by_pair.get((type_id, normal_mode))
        coach = by_pair.get((type_id, coach_mode))
        if not normal or not coach:
            continue
        different = [
            field
            for field in ACTIVITY_COVERAGE_FIELDS
            if normal["fields"][field]["status"] != coach["fields"][field]["status"]
        ]
        comparisons.append({
            "type": type_id,
            "normal_sport": normal["sport_name"],
            "coach_sport": coach["sport_name"],
            "fields_with_different_population_status": different,
        })

    next_cursor = _activity_response_next(data)
    return {
        "raw_record_count": len(records),
        "requested_fixture_count": len(ACTIVITY_CAPABILITY_FIXTURES),
        "matched_fixture_count": sum(item["matched"] for item in activities),
        "all_fixture_identities_match": all(
            not item["matched"] or item["identity_matches_expected_pair"]
            for item in activities
        ),
        "pagination": {
            "next": next_cursor,
            "terminal_single_page_observed": next_cursor == -1,
            "followed": False,
        },
        "status_legend": {
            "PRESENT_WITH_VALUE": "nonempty value present",
            "PRESENT_SENTINEL": "reserved for field-specific proven sentinels",
            "PRESENT_EMPTY": "key present without a value",
            "ABSENT": "key absent",
            "UNKNOWN_SEMANTICS": (
                "known candidate sentinel present; raw value retained"
            ),
        },
        "activities": activities,
        "coach_mode_comparisons": comparisons,
    }


def format_sport_coverage_mapping_list(inventory: dict[str, Any]) -> str:
    lines: list[str] = []
    for group in inventory.get("type_groups", []):
        duration = group.get("representative_duration")
        if isinstance(duration, (int, float)):
            total_seconds = int(round(duration))
            duration_text = (
                f"{total_seconds // 3600:02d}:"
                f"{(total_seconds % 3600) // 60:02d}:"
                f"{total_seconds % 60:02d}"
            )
        else:
            duration_text = "unknown"
        distance = group.get("representative_distance")
        distance_text = (
            f"{distance / 1000:.2f} km"
            if isinstance(distance, (int, float))
            else "unknown"
        )
        calories = group.get("representative_calories")
        calories_text = (
            f"{calories:g} kcal"
            if isinstance(calories, (int, float))
            else "unknown"
        )
        local_date = group.get("representative_local_date") or "unknown-date"
        local_time = group.get("representative_local_time") or "unknown-time"
        lines.append(
            f"type={group.get('type')} sport_mode={group.get('sport_mode')} | "
            f"end={local_date} {local_time} | duration={duration_text} | "
            f"distance={distance_text} | calories={calories_text} | "
            f"trackid={group.get('representative_trackid')}"
        )
    return "\n".join(lines)


def _activity_record_by_track_id(
    data: Any, track_id: str | int
) -> dict[str, Any] | None:
    wanted = str(track_id)
    return next(
        (
            record
            for record in _activity_records(data)
            if str(
                record.get(
                    "trackid",
                    record.get("trackId", record.get("track_id", "")),
                )
            ) == wanted
        ),
        None,
    )


def _activity_structure_paths(
    value: Any, *, path: str = "$", depth: int = 0
) -> dict[str, str]:
    value_type = (
        "list"
        if isinstance(value, list)
        else "object"
        if isinstance(value, dict)
        else type(value).__name__
    )
    paths = {path: value_type}
    if depth >= 4:
        return paths
    if isinstance(value, dict):
        for raw_key, item in value.items():
            key = str(raw_key)
            if _activity_sensitive_key(key):
                continue
            paths.update(
                _activity_structure_paths(
                    item, path=f"{path}.{key}", depth=depth + 1
                )
            )
    elif isinstance(value, list) and value:
        paths.update(
            _activity_structure_paths(
                value[0], path=f"{path}[]", depth=depth + 1
            )
        )
    return paths


def _activity_safe_leaf_values(value: Any, *, path: str = "$") -> dict[str, Any]:
    leaves: dict[str, Any] = {}
    if isinstance(value, dict):
        for key, item in value.items():
            leaves.update(
                _activity_safe_leaf_values(item, path=f"{path}.{key}")
            )
    elif isinstance(value, list):
        for index, item in enumerate(value[:3]):
            leaves.update(
                _activity_safe_leaf_values(item, path=f"{path}[{index}]")
            )
    elif isinstance(value, (int, float, bool)) or value is None:
        leaves[path] = value
    return leaves


def compare_activity_sub_data_payloads(
    without_sub_data: Any,
    with_sub_data: Any,
    *,
    sport_segment: str,
    track_id: str | int,
) -> dict[str, Any]:
    """Compare one activity structurally without exposing text or coordinates."""
    before_record = _activity_record_by_track_id(without_sub_data, track_id)
    after_record = _activity_record_by_track_id(with_sub_data, track_id)
    before_diagnostic = diagnose_activity_payload(
        without_sub_data,
        sport_segment=sport_segment,
        limit=1,
        include_text=False,
        track_id=track_id,
    )
    after_diagnostic = diagnose_activity_payload(
        with_sub_data,
        sport_segment=sport_segment,
        limit=1,
        include_text=False,
        track_id=track_id,
    )
    before_paths = (
        _activity_structure_paths(before_record) if before_record is not None else {}
    )
    after_paths = (
        _activity_structure_paths(after_record) if after_record is not None else {}
    )
    before_summary = (
        before_diagnostic["records"][0] if before_diagnostic["records"] else {}
    )
    after_summary = (
        after_diagnostic["records"][0] if after_diagnostic["records"] else {}
    )
    before_values = _activity_safe_leaf_values(before_summary)
    after_values = _activity_safe_leaf_values(after_summary)
    common_paths = sorted(before_paths.keys() & after_paths.keys())
    common_values = sorted(before_values.keys() & after_values.keys())
    return {
        "sport_segment": sport_segment,
        "track_id": str(track_id),
        "without_sub_data": before_diagnostic,
        "with_sub_data": after_diagnostic,
        "diff": {
            "structure_paths_added": sorted(after_paths.keys() - before_paths.keys()),
            "structure_paths_removed": sorted(before_paths.keys() - after_paths.keys()),
            "structure_types_changed": [
                {
                    "path": path,
                    "without": before_paths[path],
                    "with": after_paths[path],
                }
                for path in common_paths
                if before_paths[path] != after_paths[path]
            ],
            "safe_values_changed": [
                {
                    "path": path,
                    "without": before_values[path],
                    "with": after_values[path],
                }
                for path in common_values
                if before_values[path] != after_values[path]
            ],
        },
    }


ACTIVITY_DETAIL_STREAM_FIELDS = (
    "longitude_latitude", "time", "altitude", "air_pressure_altitude",
    "correct_altitude", "heart_rate", "speed", "pace", "gait", "cadence",
    "power_meter", "lap", "kilo_pace", "mile_pace", "stroke_speed",
    "coaching_segment", "pause", "distance", "accuracy", "spo2", "flag",
    "bearing", "course", "daily_performance_info", "divingDepth",
    "temperature",
    "rope_skipping_frequency", "weather_info", "golf_swing_rt_data",
)
ACTIVITY_DETAIL_TEXT_KEYS = {
    "name", "title", "description", "note", "notes", "remark", "remarks",
    "comment", "comments", "memo", "content", "sport_note", "workout_note",
    "workoutnotes", "track_note", "tracknote", "user_note", "usernote",
}
CANONICAL_METRIC_STATUSES = {
    "AVAILABLE",
    "SUPPORTED_BUT_NOT_RECORDED",
    "NOT_APPLICABLE",
    "UNSUPPORTED",
    "SENTINEL_UNAVAILABLE",
    "INVALID",
    "UNKNOWN",
}
ACTIVITY_DETAIL_SENTINELS = {
    # Production-proven for Open Water Swim detail.altitude in Z001.9.
    "altitude": {-2000000},
}
ACTIVITY_DETAIL_SCHEMA_ONLY_FIELDS = {
    "strengthAssess", "strengthSets", "hyroxBlocks", "hyroxRepLap",
    "hyroxTimelines",
}
ACTIVITY_DETAIL_PRODUCTION_PROVEN_PAIRS = {
    (196, 0),   # Outdoor Free Diving
    (22, 0),    # Hiking / Ojstrica
    (130, 0),   # Cross-training
    (14, 0),    # Pool Swim
    (15, 0),    # Open Water Swim
    (208, 0),   # Gravel Cycling
    (105, 0),   # Ski
}
ACTIVITY_ALTITUDE_SCALE_PRODUCTION_PROVEN_PAIRS = {
    (22, 0),
    (208, 0),
    (105, 0),
}
ACTIVITY_DETAIL_STREAM_FIELDS = tuple(dict.fromkeys(
    ACTIVITY_DETAIL_STREAM_FIELDS + (
        "pool_swim_pace", "pool_stroke_speed", "currentDistance",
        *sorted(ACTIVITY_DETAIL_SCHEMA_ONLY_FIELDS),
    )
))


def _canonical_provenance(
    endpoint: str,
    source_path: str,
    *,
    raw_encoding: str = "scalar",
    normalization: str = "identity",
    semantic_rule: str | None = None,
    confidence: str = "PRODUCTION_PROVEN",
) -> dict[str, Any]:
    return {
        "endpoint": endpoint,
        "source_path": source_path,
        "raw_encoding": raw_encoding,
        "normalization": normalization,
        "semantic_rule": semantic_rule,
        "confidence": confidence,
    }


def _canonical_metric(
    value: Any,
    unit: str | None,
    status: str,
    *,
    raw_value: Any = None,
    provenance: dict[str, Any] | None = None,
    semantic_confidence: str = "PRODUCTION_PROVEN",
    reason: str | None = None,
) -> dict[str, Any]:
    if status not in CANONICAL_METRIC_STATUSES:
        raise ValueError(f"Unknown canonical metric status: {status}")
    metric = {
        "value": value,
        "unit": unit,
        "status": status,
        "raw_value": raw_value,
        "provenance": provenance,
        "semantic_confidence": semantic_confidence,
    }
    if reason:
        metric["reason"] = reason
    return metric


def _canonical_detail_payload(detail_response: Any) -> dict[str, Any] | None:
    if not isinstance(detail_response, dict):
        return None
    payload = detail_response.get("data")
    return payload if isinstance(payload, dict) else None


def _canonical_memo_text(memo: Any) -> tuple[str | None, str | None]:
    """Return the supported human-authored Workout Notes text and provenance."""
    if isinstance(memo, dict):
        summary = memo.get("summary")
        if isinstance(summary, str) and summary.strip():
            return summary.strip(), "detail.memo.summary"
        return None, None
    if not isinstance(memo, str) or not memo.strip():
        return None, None

    # Production detail.memo is a JSON-encoded Zepp envelope. Require its
    # observed envelope fields so ordinary JSON-like user text stays literal.
    try:
        decoded = json.loads(memo)
    except json.JSONDecodeError:
        return memo, "detail.memo"
    if (
        isinstance(decoded, dict)
        and "groupMemos" in decoded
        and "onlyUpdateSummary" in decoded
    ):
        summary = decoded.get("summary")
        if isinstance(summary, str) and summary.strip():
            return summary.strip(), "detail.memo.summary"
        return None, None
    return memo, "detail.memo"


def _canonical_sport_capabilities(
    mapping: dict[str, Any] | None,
) -> dict[str, str]:
    if mapping is None:
        return {
            key: "UNKNOWN"
            for key in (
                "gps", "altitude", "depth", "heart_rate", "temperature",
                "cadence", "power", "laps",
            )
        }
    family = mapping["sport_family"]
    name = mapping["sport_name"]
    gps = (
        "NOT_APPLICABLE"
        if name in {"Pool Swim", "Cross-training"}
        else "EXPECTED"
    )
    altitude = (
        "NOT_APPLICABLE"
        if family in {"Swimming", "Cross-training", "Free Diving"}
        else "SUPPORTED"
    )
    return {
        "gps": gps,
        "altitude": altitude,
        "depth": "SUPPORTED" if family == "Free Diving" else "NOT_APPLICABLE",
        "heart_rate": "SUPPORTED",
        "temperature": "SUPPORTED" if family == "Free Diving" else "UNKNOWN",
        "cadence": "SUPPORTED_OPTIONAL_SENSOR" if family == "Cycling"
        else "NOT_APPLICABLE",
        "power": "SUPPORTED_OPTIONAL_SENSOR" if family == "Cycling"
        else "NOT_APPLICABLE",
        "laps": "SUPPORTED"
        if name in {"Pool Swim", "Outdoor Free Diving"} else "UNKNOWN",
    }


def _canonical_numeric_entries(value: Any) -> tuple[list[Any], list[float], bool]:
    raw_entries = _detail_entries(value)
    numeric: list[float] = []
    valid = True
    for item in raw_entries:
        if isinstance(item, bool):
            valid = False
            continue
        try:
            number = float(item)
        except (TypeError, ValueError):
            valid = False
            continue
        if not math.isfinite(number):
            valid = False
            continue
        numeric.append(number)
    return raw_entries, numeric, valid and len(raw_entries) == len(numeric)


def _canonical_time_offsets(value: Any) -> tuple[list[float], bool]:
    raw_entries, deltas, valid = _canonical_numeric_entries(value)
    if not raw_entries or not valid:
        return [], False
    offsets: list[float] = []
    elapsed = 0.0
    for delta in deltas:
        if delta < 0:
            return [], False
        elapsed += delta
        offsets.append(elapsed)
    return offsets, all(
        later >= earlier for earlier, later in zip(offsets, offsets[1:])
    )


def _canonical_gps_stream(
    payload: dict[str, Any],
    capability: str,
    *,
    evidence_confidence: str,
) -> dict[str, Any]:
    entries = _detail_entries(payload.get("longitude_latitude"))
    if not entries:
        status = "NOT_APPLICABLE" if capability == "NOT_APPLICABLE" else "UNKNOWN"
        return {
            "status": status,
            "sample_count": 0,
            "samples": [],
            "source_path": "detail.longitude_latitude",
            "semantic_confidence": evidence_confidence,
        }
    time_entries = _detail_entries(payload.get("time"))
    offsets, offsets_valid = _canonical_time_offsets(payload.get("time"))
    samples: list[dict[str, Any]] = []
    longitude_raw = 0
    latitude_raw = 0
    valid = True
    for index, entry in enumerate(entries):
        if not isinstance(entry, str):
            valid = False
            break
        parts = entry.split(",")
        if len(parts) < 2:
            valid = False
            break
        try:
            first = int(parts[0])
            second = int(parts[1])
        except ValueError:
            valid = False
            break
        longitude_raw = first if index == 0 else longitude_raw + first
        latitude_raw = second if index == 0 else latitude_raw + second
        longitude = longitude_raw / 100_000_000
        latitude = latitude_raw / 100_000_000
        if not (-180 <= longitude <= 180 and -90 <= latitude <= 90):
            valid = False
            break
        samples.append({
            "offset_s": offsets[index] if offsets_valid and index < len(offsets) else None,
            "longitude": longitude,
            "latitude": latitude,
            "raw": [first, second],
        })
    return {
        "status": "AVAILABLE" if valid and len(samples) == len(entries) else "INVALID",
        "sample_count": len(entries),
        "samples": samples if valid and len(samples) == len(entries) else [],
        "source_path": "detail.longitude_latitude",
        "timestamp_source_path": "detail.time" if offsets_valid else None,
        "timestamp_alignment_status": (
            "MATCHED"
            if offsets_valid and len(offsets) == len(entries)
            else (
                "INVALID"
                if time_entries and not offsets_valid
                else ("UNAVAILABLE" if not time_entries else "COUNT_MISMATCH")
            )
        ),
        "semantic_confidence": evidence_confidence,
        "provenance": _canonical_provenance(
            "/v1/sport/run/detail.json",
            "detail.longitude_latitude",
            raw_encoding="semicolon delta coordinate pairs",
            normalization="delta accumulation then /1e8",
            confidence=evidence_confidence,
        ),
    }


def _canonical_altitude_stream(
    payload: dict[str, Any],
    capability: str,
    *,
    scaling_confidence: str,
) -> dict[str, Any]:
    raw_entries, values, valid = _canonical_numeric_entries(payload.get("altitude"))
    if not raw_entries:
        status = "NOT_APPLICABLE" if capability == "NOT_APPLICABLE" else "UNKNOWN"
        return {
            "status": status,
            "sample_count": 0,
            "samples": [],
            "source_path": "detail.altitude",
            "semantic_confidence": scaling_confidence,
        }
    sentinel_values = ACTIVITY_DETAIL_SENTINELS["altitude"]
    if valid and values and all(value in sentinel_values for value in values):
        return {
            "status": "SENTINEL_UNAVAILABLE",
            "sample_count": len(raw_entries),
            "samples": [
                {
                    "raw_value": raw_value,
                    "value_m": None,
                    "status": "SENTINEL_UNAVAILABLE",
                }
                for raw_value in raw_entries
            ],
            "source_path": "detail.altitude",
            "semantic_confidence": "PRODUCTION_PROVEN",
            "reason": "production_proven_open_water_altitude_sentinel",
            "provenance": _canonical_provenance(
                "/v1/sport/run/detail.json",
                "detail.altitude",
                raw_encoding="semicolon numeric records",
                normalization="sentinel classification before scaling",
                confidence="PRODUCTION_PROVEN",
            ),
        }
    if not valid or any(value in sentinel_values for value in values):
        return {
            "status": "INVALID",
            "sample_count": len(raw_entries),
            "samples": [],
            "raw_values": raw_entries,
            "source_path": "detail.altitude",
            "semantic_confidence": "UNKNOWN",
            "reason": "mixed_sentinel_or_nonnumeric_altitude_stream",
        }
    return {
        "status": "AVAILABLE",
        "sample_count": len(values),
        "samples": [
            {
                "raw_value": raw_value,
                "value_m": value / 100,
                "status": "AVAILABLE",
            }
            for raw_value, value in zip(raw_entries, values)
        ],
        "source_path": "detail.altitude",
        "unit": "m",
        "scale": 0.01,
        "semantic_confidence": scaling_confidence,
        "provenance": _canonical_provenance(
            "/v1/sport/run/detail.json",
            "detail.altitude",
            raw_encoding="semicolon numeric records",
            normalization="raw / 100 metres",
            confidence=scaling_confidence,
        ),
    }


def _canonical_delta_pair_stream(
    payload: dict[str, Any],
    field: str,
    *,
    capability: str = "SUPPORTED",
    unit: str | None = None,
    evidence_confidence: str = "OBSERVED_IN_PUBLIC_IMPLEMENTATION",
) -> dict[str, Any]:
    entries = _detail_entries(payload.get(field))
    if not entries:
        if capability == "NOT_APPLICABLE":
            status = "NOT_APPLICABLE"
        elif capability == "SUPPORTED_OPTIONAL_SENSOR":
            status = "SUPPORTED_BUT_NOT_RECORDED"
        else:
            status = "UNKNOWN"
        return {
            "status": status,
            "sample_count": 0,
            "samples": [],
            "source_path": f"detail.{field}",
            "semantic_confidence": evidence_confidence,
        }
    elapsed = 0
    current = 0
    samples: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, str):
            samples = []
            break
        parts = entry.split(",")
        if len(parts) < 2:
            samples = []
            break
        try:
            delta_time = int(parts[0] or 1)
            if delta_time < 0:
                samples = []
                break
            elapsed += delta_time
            current += int(parts[1])
        except ValueError:
            samples = []
            break
        samples.append({
            "offset_s": elapsed,
            "value": current,
            "raw": [parts[0], parts[1]],
        })
    return {
        "status": "AVAILABLE" if len(samples) == len(entries) else "INVALID",
        "sample_count": len(entries),
        "samples": samples if len(samples) == len(entries) else [],
        "source_path": f"detail.{field}",
        "unit": unit,
        "semantic_confidence": evidence_confidence,
        "provenance": _canonical_provenance(
            "/v1/sport/run/detail.json",
            f"detail.{field}",
            raw_encoding="semicolon delta-time/delta-value pairs",
            normalization="independent delta accumulation",
            confidence=(
                evidence_confidence
            ),
        ),
    }


def _canonical_float_delta_stream(
    payload: dict[str, Any],
    field: str,
    *,
    capability: str,
    unit: str,
    positive_only: bool = False,
    semantic_rule: str | None = None,
) -> dict[str, Any]:
    """Decode a native delta-time/delta-value stream without integer coercion."""
    entries = _detail_entries(payload.get(field))
    if not entries:
        return {
            "status": "NOT_APPLICABLE" if capability == "NOT_APPLICABLE" else "UNKNOWN",
            "sample_count": 0,
            "samples": [],
            "source_path": f"detail.{field}",
            "semantic_confidence": "PRODUCTION_PROVEN",
        }
    elapsed = 0
    current = 0.0
    samples: list[dict[str, Any]] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, str):
            samples = []
            break
        parts = entry.split(",")
        if len(parts) < 2:
            samples = []
            break
        try:
            delta_time = int(parts[0])
            component = float(parts[1])
        except ValueError:
            samples = []
            break
        if delta_time < 0 or not math.isfinite(component):
            samples = []
            break
        elapsed += delta_time
        current = component if index == 0 else current + component
        if positive_only and current < -0.00001:
            samples = []
            break
        samples.append({
            "offset_s": elapsed,
            "value": max(0.0, current) if positive_only else current,
            "status": "AVAILABLE",
            "raw": parts,
        })
    valid = len(samples) == len(entries)
    return {
        "status": "AVAILABLE" if valid else "INVALID",
        "sample_count": len(entries),
        "samples": samples if valid else [],
        "source_path": f"detail.{field}",
        "unit": unit,
        "semantic_confidence": "PRODUCTION_PROVEN",
        "provenance": _canonical_provenance(
            "/v1/sport/run/detail.json",
            f"detail.{field}",
            raw_encoding="semicolon delta-time/delta-value records",
            normalization="independent floating-point delta accumulation",
            semantic_rule=semantic_rule,
        ),
    }


def _canonical_structural_stream(
    payload: dict[str, Any],
    field: str,
    *,
    capability: str = "UNKNOWN",
) -> dict[str, Any]:
    entries = _detail_entries(payload.get(field))
    if not entries:
        if capability == "NOT_APPLICABLE":
            status = "NOT_APPLICABLE"
        elif capability == "SUPPORTED_OPTIONAL_SENSOR":
            status = "SUPPORTED_BUT_NOT_RECORDED"
        else:
            status = "UNKNOWN"
        return {
            "status": status,
            "sample_count": 0,
            "records": [],
            "source_path": f"detail.{field}",
            "semantic_confidence": "UNKNOWN",
        }
    numeric_entries = [_activity_number(item) for item in entries]
    if (
        numeric_entries
        and all(number is not None for number in numeric_entries)
        and all(
            _activity_is_unavailable_sentinel(number)
            for number in numeric_entries
        )
    ):
        return {
            "status": "SENTINEL_UNAVAILABLE",
            "sample_count": len(entries),
            "records": [{"raw_value": item} for item in entries],
            "source_path": f"detail.{field}",
            "semantic_confidence": "PRODUCTION_PROVEN",
            "reason": "known_activity_sentinel_values",
        }
    records = []
    for entry in entries:
        if isinstance(entry, str):
            records.append({"raw_components": entry.split(",")})
        else:
            records.append({"raw_type": type(entry).__name__})
    return {
        "status": "AVAILABLE",
        "sample_count": len(entries),
        "records": records,
        "source_path": f"detail.{field}",
        "semantic_confidence": "UNKNOWN",
        "provenance": _canonical_provenance(
            "/v1/sport/run/detail.json",
            f"detail.{field}",
            raw_encoding="semicolon records",
            normalization="structural preservation only",
            confidence="SCHEMA_DISCOVERED",
        ),
    }


def _canonical_history_metric(
    record: dict[str, Any],
    field: str,
    *,
    unit: str | None,
    semantic_confidence: str = "PRODUCTION_PROVEN",
) -> dict[str, Any]:
    raw = record.get(field)
    value = _activity_usable_metric_number(raw)
    if _activity_is_unavailable_sentinel(raw):
        status = "SENTINEL_UNAVAILABLE"
    elif value is not None:
        status = "AVAILABLE"
    elif field in record:
        status = "INVALID" if raw not in (None, "") else "UNKNOWN"
    else:
        status = "UNKNOWN"
    return _canonical_metric(
        value,
        unit,
        status,
        raw_value=raw if field in record else None,
        provenance=_canonical_provenance(
            "/v1/sport/run/history.json",
            f"history.summary.{field}",
            confidence=semantic_confidence,
        ),
        semantic_confidence=semantic_confidence if value is not None else "UNKNOWN",
    )


def _canonical_history_millis_metric(
    record: dict[str, Any], field: str,
) -> dict[str, Any]:
    raw = record.get(field)
    value = _activity_usable_metric_number(raw)
    if _activity_is_unavailable_sentinel(raw):
        status = "SENTINEL_UNAVAILABLE"
        seconds = None
    elif value is not None:
        status = "AVAILABLE"
        seconds = value / 1000
    else:
        status = "INVALID" if field in record and raw not in (None, "") else "UNKNOWN"
        seconds = None
    return _canonical_metric(
        seconds,
        "s",
        status,
        raw_value=raw if field in record else None,
        provenance=_canonical_provenance(
            "/v1/sport/run/history.json",
            f"history.summary.{field}",
            normalization="milliseconds / 1000",
        ),
        semantic_confidence="PRODUCTION_PROVEN" if seconds is not None else "UNKNOWN",
    )


def _canonical_activity_time(
    history_record: dict[str, Any],
    timezone_name: str,
) -> dict[str, Any]:
    zone = ZoneInfo(timezone_name)
    track_id = _activity_number(history_record.get("trackid"))
    start: datetime | None = None
    if isinstance(track_id, (int, float)) and 946684800 <= track_id <= 4102444800:
        start = datetime.fromtimestamp(track_id, zone)
    end_raw = _activity_number(history_record.get("end_time"))
    end: datetime | None = None
    if isinstance(end_raw, (int, float)):
        if end_raw >= 1_000_000_000_000:
            end_raw /= 1000
        if 946684800 <= end_raw <= 4102444800:
            end = datetime.fromtimestamp(end_raw, zone)
    duration, duration_field = _activity_duration_seconds(history_record)
    return {
        "timezone": timezone_name,
        "start_time": start.isoformat() if start else None,
        "end_time": end.isoformat() if end else None,
        "local_activity_date": start.date().isoformat() if start else None,
        "duration_s": _canonical_metric(
            duration,
            "s",
            "AVAILABLE" if duration is not None else "UNKNOWN",
            raw_value=history_record.get(duration_field) if duration_field else None,
            provenance=(
                _canonical_provenance(
                    "/v1/sport/run/history.json",
                    f"history.summary.{duration_field}",
                    normalization=(
                        "identity seconds"
                        if duration_field == "run_time"
                        else "milliseconds / 1000"
                    ),
                )
                if duration_field else None
            ),
        ),
        "start_time_provenance": (
            _canonical_provenance(
                "/v1/sport/run/history.json",
                "history.summary.trackid",
                normalization="Unix epoch seconds to requested timezone",
                confidence="INFERRED",
            )
            if start else None
        ),
    }


def canonicalize_activity(
    history_record: dict[str, Any],
    detail_response: Any,
    *,
    timezone_name: str = FRESHNESS_TIMEZONE,
) -> dict[str, Any]:
    """Merge Zepp history and native detail without overwriting raw evidence."""
    payload = _canonical_detail_payload(detail_response)
    mapping = _activity_sport_mapping(history_record)
    capabilities = _canonical_sport_capabilities(mapping)
    mapping_key = (
        (mapping["type"], mapping["sport_mode"]) if mapping is not None else None
    )
    detail_confidence = (
        "PRODUCTION_PROVEN"
        if mapping_key in ACTIVITY_DETAIL_PRODUCTION_PROVEN_PAIRS
        else "OBSERVED_IN_PUBLIC_IMPLEMENTATION"
    )
    altitude_scaling_confidence = (
        "PRODUCTION_PROVEN"
        if mapping_key in ACTIVITY_ALTITUDE_SCALE_PRODUCTION_PROVEN_PAIRS
        else "OBSERVED_IN_PUBLIC_IMPLEMENTATION"
    )
    history_track_id = history_record.get("trackid", history_record.get("trackId"))
    detail_track_id = (
        payload.get("trackid", payload.get("trackId")) if payload is not None else None
    )
    detail_recognized = payload is not None
    track_match = (
        detail_recognized
        and detail_track_id is not None
        and str(history_track_id) == str(detail_track_id)
    )

    quality_flags: list[str] = []
    if not detail_recognized:
        quality_flags.append("DETAIL_WRAPPER_UNRECOGNIZED")
    elif detail_track_id is None:
        quality_flags.append("DETAIL_TRACK_ID_MISSING")
    elif not track_match:
        quality_flags.append("HISTORY_DETAIL_TRACK_ID_MISMATCH")

    # Never enrich one history activity with another activity's detail payload.
    payload_for_streams = payload if track_match else {}
    gps = _canonical_gps_stream(
        payload_for_streams,
        capabilities["gps"],
        evidence_confidence=detail_confidence,
    )
    altitude = _canonical_altitude_stream(
        payload_for_streams,
        capabilities["altitude"],
        scaling_confidence=altitude_scaling_confidence,
    )
    depth = _canonical_float_delta_stream(
        payload_for_streams,
        "divingDepth",
        capability=capabilities["depth"],
        unit="m",
        positive_only=True,
        semantic_rule="positive distance below surface; never altitude or elevation",
    )
    heart_rate = _canonical_delta_pair_stream(
        payload_for_streams,
        "heart_rate",
        unit="bpm",
        evidence_confidence=detail_confidence,
    )
    observation_capabilities = capabilities if track_match else {
        **capabilities,
        "cadence": "UNKNOWN",
        "power": "UNKNOWN",
    }
    cadence = _canonical_structural_stream(
        payload_for_streams,
        "cadence",
        capability=observation_capabilities["cadence"],
    )
    power = _canonical_structural_stream(
        payload_for_streams,
        "power_meter",
        capability=observation_capabilities["power"],
    )
    if gps["status"] == "AVAILABLE":
        quality_flags.append("GPS_STREAM_AVAILABLE")
    elif capabilities["gps"] == "EXPECTED" and track_match:
        quality_flags.append("GPS_STREAM_MISSING")
    if gps.get("timestamp_alignment_status") == "COUNT_MISMATCH":
        quality_flags.append("GPS_TIME_COUNT_MISMATCH")
    elif gps.get("timestamp_alignment_status") == "INVALID":
        quality_flags.append("GPS_TIME_INVALID")
    if altitude["status"] == "SENTINEL_UNAVAILABLE":
        quality_flags.append("ALTITUDE_SENTINEL")
    if power["status"] == "SUPPORTED_BUT_NOT_RECORDED":
        quality_flags.append("POWER_SENSOR_NOT_RECORDED")

    detail_note = payload_for_streams.get("memo")
    history_note = history_record.get("sportNotes")
    notes_text, notes_source_path = _canonical_memo_text(detail_note)
    if notes_text is None and isinstance(history_note, str) and history_note.strip():
        notes_text = history_note
        notes_source_path = "history.summary.sportNotes"
    notes_present = isinstance(notes_text, str) and bool(notes_text)
    if notes_present:
        quality_flags.append("WORKOUT_NOTES_AVAILABLE")

    semantics = interpret_activity_metrics(history_record)
    normalized = semantics.get("normalized_metrics", {})
    distance = normalized.get("distance_m", {})
    calories = normalized.get("calories_kcal", {})
    def semantic_metric(name: str, *, unit: str = "m") -> dict[str, Any]:
        item = normalized.get(name)
        if not isinstance(item, dict):
            if mapping is None:
                missing_status = "UNKNOWN"
            elif name == "vertical_descent_m" or (
                name == "ski_vertical_m" and is_alpine_ski_activity(history_record)
            ):
                missing_status = "NOT_APPLICABLE"
            elif mapping["sport_family"] in {
                "Swimming", "Cross-training", "Free Diving",
            }:
                missing_status = "NOT_APPLICABLE"
            else:
                missing_status = "UNKNOWN"
            return _canonical_metric(None, unit, missing_status)
        source_field = item.get("source_field")
        raw_value = history_record.get(source_field) if source_field else None
        if source_field and _activity_is_unavailable_sentinel(raw_value):
            status = "SENTINEL_UNAVAILABLE"
        elif item.get("value") is not None:
            status = "AVAILABLE"
        elif item.get("reason") == "ski_lift_ascent_is_not_athlete_powered_climbing":
            status = "NOT_APPLICABLE"
        else:
            status = "UNKNOWN"
        return _canonical_metric(
            item.get("value"),
            unit,
            status,
            raw_value=raw_value,
            provenance=(
                _canonical_provenance(
                    "/v1/sport/run/history.json",
                    f"history.summary.{source_field}",
                    semantic_rule=item.get("reason"),
                    confidence=item.get("semantic_confidence", "UNKNOWN"),
                )
                if source_field else None
            ),
            semantic_confidence=item.get("semantic_confidence", "UNKNOWN"),
            reason=item.get("reason"),
        )

    time_model = _canonical_activity_time(history_record, timezone_name)
    summary = {
        "duration_s": time_model["duration_s"],
        "distance_m": _canonical_metric(
            distance.get("value"),
            "m",
            "AVAILABLE" if distance.get("value") is not None else "UNKNOWN",
            raw_value=history_record.get(distance.get("source_field"))
            if distance.get("source_field") else None,
            provenance=(
                _canonical_provenance(
                    "/v1/sport/run/history.json",
                    f"history.summary.{distance.get('source_field')}",
                    semantic_rule="established sport-aware distance precedence",
                )
                if distance.get("source_field") else None
            ),
            semantic_confidence=distance.get("semantic_confidence", "UNKNOWN"),
        ),
        "calories_kcal": _canonical_metric(
            calories.get("value"),
            "kcal",
            "AVAILABLE" if calories.get("value") is not None else "UNKNOWN",
            raw_value=history_record.get("calorie"),
            provenance=_canonical_provenance(
                "/v1/sport/run/history.json", "history.summary.calorie"
            ),
            semantic_confidence=calories.get("semantic_confidence", "UNKNOWN"),
        ),
        "heart_rate_avg_bpm": _canonical_history_metric(
            history_record, "avg_heart_rate", unit="bpm"
        ),
        "heart_rate_min_bpm": _canonical_history_metric(
            history_record, "min_heart_rate", unit="bpm"
        ),
        "heart_rate_max_bpm": _canonical_history_metric(
            history_record, "max_heart_rate", unit="bpm"
        ),
        "training_load": _canonical_history_metric(
            history_record, "exercise_load", unit="Zepp load"
        ),
        "aerobic_training_effect": _canonical_history_metric(
            history_record,
            "te",
            unit="Zepp raw TE",
            semantic_confidence="UNKNOWN",
        ),
        "anaerobic_training_effect": _canonical_history_metric(
            history_record,
            "anaerobic_te",
            unit="Zepp raw TE",
            semantic_confidence="UNKNOWN",
        ),
        "rpe": _canonical_history_metric(
            history_record, "rpe", unit="Zepp RPE"
        ),
        "average_pace": _canonical_history_metric(
            history_record,
            "avg_pace",
            unit="Zepp raw pace",
            semantic_confidence="UNKNOWN",
        ),
        "maximum_pace": _canonical_history_metric(
            history_record,
            "max_pace",
            unit="Zepp raw pace",
            semantic_confidence="UNKNOWN",
        ),
        "average_cadence": _canonical_history_metric(
            history_record,
            "avg_cadence",
            unit="sport-specific cadence",
            semantic_confidence="INFERRED",
        ),
        "maximum_cadence": _canonical_history_metric(
            history_record,
            "max_cadence",
            unit="sport-specific cadence",
            semantic_confidence="INFERRED",
        ),
        "average_power_w": _canonical_history_metric(
            history_record, "average_power", unit="W"
        ),
        "maximum_power_w": _canonical_history_metric(
            history_record, "max_power", unit="W"
        ),
        "downhill_run_count": _canonical_history_metric(
            history_record, "downhill_num", unit="count"
        ),
        "reported_elevation_gain_m": semantic_metric("elevation_gain_m"),
        "reported_elevation_loss_m": semantic_metric("elevation_loss_m"),
        # Stable canonical names. The reported_* aliases above remain for
        # compatibility with the first canonical activity contract.
        "elevation_gain_m": semantic_metric("elevation_gain_m"),
        "elevation_loss_m": semantic_metric("elevation_loss_m"),
        "vertical_descent_m": semantic_metric("vertical_descent_m"),
        "ski_vertical_m": semantic_metric("ski_vertical_m"),
        "derived_elevation_gain_m": _canonical_metric(
            None,
            "m",
            "UNKNOWN",
            reason="future_altitude_validation_slot",
        ),
    }
    if mapping is not None and mapping["sport_family"] == "Free Diving":
        summary.update({
            "max_depth_m": _canonical_history_metric(
                history_record, "maximumDepth", unit="m"
            ),
            "average_depth_m": _canonical_history_metric(
                history_record, "divingAverageDepth", unit="m"
            ),
            "average_max_depth_m": _canonical_history_metric(
                history_record, "averageMaxDepth", unit="m"
            ),
            "dive_count": _canonical_history_metric(
                history_record, "numberOfDives", unit="count"
            ),
            "average_diving_speed_mps": _canonical_history_metric(
                history_record, "averageDiveSpeed", unit="m/s"
            ),
            "max_diving_speed_mps": _canonical_history_metric(
                history_record, "maximumDiveSpeed", unit="m/s"
            ),
            "total_dive_duration_seconds": _canonical_history_millis_metric(
                history_record, "totalDiveTimeWithMillis"
            ),
            "average_dive_duration_seconds": _canonical_history_millis_metric(
                history_record, "avgDiveTimeWithMillis"
            ),
            "maximum_dive_duration_seconds": _canonical_history_millis_metric(
                history_record, "maxDiveTimeWithMillis"
            ),
            "total_surface_recovery_seconds": _canonical_history_millis_metric(
                history_record, "totalSurfaceTimeWithMillis"
            ),
            "average_surface_recovery_seconds": _canonical_history_millis_metric(
                history_record, "avgSurfaceTimeWithMillis"
            ),
            "temperature_c": _canonical_history_metric(
                history_record, "avg_temperature", unit="C"
            ),
        })
    streams = {
        "gps": gps,
        "altitude": altitude,
        "heart_rate": heart_rate,
        "cadence": cadence,
        "power": power,
        "speed": _canonical_structural_stream(payload_for_streams, "speed"),
        "pace": _canonical_structural_stream(payload_for_streams, "pace"),
    }
    if _detail_entries(payload_for_streams.get("divingDepth")) or (
        mapping is not None and mapping["sport_family"] == "Free Diving"
    ):
        streams["depth"] = depth
    if _detail_entries(payload_for_streams.get("temperature")) or (
        mapping is not None and mapping["sport_family"] == "Free Diving"
    ):
        streams["temperature"] = _canonical_float_delta_stream(
            payload_for_streams,
            "temperature",
            capability=capabilities["temperature"],
            unit="C",
        )
    return {
        "schema_version": 1,
        "identity": {
            "track_id": history_track_id,
            "source": history_record.get("source"),
            "native_type": history_record.get("type"),
            "sport_mode": history_record.get("sport_mode"),
            "sport_name": mapping["sport_name"] if mapping else None,
            "sport_family": mapping["sport_family"] if mapping else None,
            "zepp_coach_mode": mapping["zepp_coach_mode"] if mapping else None,
            "mapping_confidence": mapping["confidence"] if mapping else "UNKNOWN",
        },
        "time": time_model,
        "sport_capabilities": capabilities,
        "summary": summary,
        "streams": streams,
        "laps": {
            "lap": _canonical_structural_stream(
                payload_for_streams, "lap", capability=capabilities["laps"]
            ),
            "pool_swim_pace": _canonical_structural_stream(
                payload_for_streams, "pool_swim_pace"
            ),
            "pool_stroke_speed": _canonical_structural_stream(
                payload_for_streams, "pool_stroke_speed"
            ),
            "current_distance": _canonical_structural_stream(
                payload_for_streams, "currentDistance"
            ),
        },
        "strength": {
            field: {
                "status": (
                    "AVAILABLE"
                    if _detail_entries(payload_for_streams.get(field))
                    else "UNKNOWN"
                ),
                "schema_evidence": "SCHEMA_DISCOVERED",
                "structure": _detail_stream_shape(payload_for_streams.get(field)),
            }
            for field in sorted(ACTIVITY_DETAIL_SCHEMA_ONLY_FIELDS)
        },
        "notes": {
            "present": notes_present,
            "length": len(notes_text) if notes_present else 0,
            "text": notes_text if notes_present else None,
            "source_path": notes_source_path,
            "evidence": (
                "PRODUCTION_PROVEN"
                if mapping_key == (130, 0)
                else "SCHEMA_DISCOVERED"
            ),
        },
        "coach": {
            "zepp_coach_mode": mapping["zepp_coach_mode"] if mapping else None,
            "segments": _canonical_structural_stream(
                payload_for_streams, "coaching_segment"
            ),
        },
        "quality": {
            "flags": sorted(set(quality_flags)),
            "history_detail_identity_match": track_match,
            "stream_alignment": "INDEPENDENT_OFFSETS_NO_INDEX_ALIGNMENT",
        },
        "provenance": {
            "history_endpoint": "/v1/sport/run/history.json",
            "detail_endpoint": "/v1/sport/run/detail.json",
            "history_raw_fields": sorted(str(key) for key in history_record),
            "detail_raw_fields": sorted(str(key) for key in payload_for_streams),
            "history_remains_summary_authority": True,
            "detail_enrichment_only": True,
        },
    }


def safe_canonical_activity(activity: dict[str, Any]) -> dict[str, Any]:
    """Serialize canonical activity without coordinates, notes, or source IDs."""
    safe = {
        "schema_version": activity.get("schema_version"),
        "identity": {
            key: value
            for key, value in activity.get("identity", {}).items()
            if key != "source"
        },
        "time": activity.get("time"),
        "sport_capabilities": activity.get("sport_capabilities"),
        "summary": activity.get("summary"),
        "streams": {},
        "laps": {},
        "strength": activity.get("strength"),
        "notes": {
            "present": activity.get("notes", {}).get("present", False),
            "length": activity.get("notes", {}).get("length", 0),
            "source_path": activity.get("notes", {}).get("source_path"),
            "text_suppressed": True,
        },
        "coach": {
            "zepp_coach_mode": activity.get("coach", {}).get("zepp_coach_mode"),
            "segments": {
                key: value
                for key, value in activity.get("coach", {}).get(
                    "segments", {}
                ).items()
                if key not in {"records", "samples"}
            },
        },
        "quality": activity.get("quality"),
        "provenance": activity.get("provenance"),
        "privacy": (
            "Coordinates, sample values, Workout Notes text, source values, "
            "credentials, user/device identifiers, URLs, and raw payloads omitted."
        ),
    }
    for name, stream in activity.get("streams", {}).items():
        values = []
        if name == "altitude":
            values = [
                sample.get("value_m")
                for sample in stream.get("samples", [])
                if sample.get("value_m") is not None
            ]
        elif name == "heart_rate":
            values = [
                sample.get("value")
                for sample in stream.get("samples", [])
                if sample.get("value") is not None
            ]
        safe["streams"][name] = {
            key: value
            for key, value in stream.items()
            if key not in {"samples", "records", "raw_values"}
        }
        if values:
            safe["streams"][name]["minimum"] = min(values)
            safe["streams"][name]["maximum"] = max(values)
        safe["streams"][name]["sample_values_suppressed"] = True
    for name, structure in activity.get("laps", {}).items():
        safe["laps"][name] = {
            key: value
            for key, value in structure.items()
            if key not in {"records", "samples"}
        }
        safe["laps"][name]["record_values_suppressed"] = True
    return safe


def _detail_entries(value: Any) -> list[Any]:
    if isinstance(value, str):
        return [item for item in value.split(";") if item != ""]
    if isinstance(value, list):
        return value
    return [] if value in (None, "", {}, []) else [value]


def _detail_numeric_series(value: Any) -> list[float]:
    numbers: list[float] = []
    for item in _detail_entries(value):
        if isinstance(item, bool):
            continue
        try:
            numbers.append(float(item))
        except (TypeError, ValueError):
            continue
    return numbers


def _detail_coordinate_sample_count(value: Any) -> tuple[int, bool]:
    entries = _detail_entries(value)
    valid_count = 0
    for item in entries:
        if not isinstance(item, str):
            continue
        components = item.split(",")
        if len(components) < 2:
            continue
        try:
            float(components[0])
            float(components[1])
        except ValueError:
            continue
        valid_count += 1
    return valid_count, valid_count == len(entries)


def _detail_stream_shape(value: Any) -> dict[str, Any]:
    entries = _detail_entries(value)
    widths: set[int] = set()
    for item in entries[:100]:
        if isinstance(item, str):
            widths.add(len(item.split(",")))
        elif isinstance(item, (list, tuple, dict)):
            widths.add(len(item))
        else:
            widths.add(1)
    return {
        "present": bool(entries),
        "sample_count": len(entries),
        "encoded_length": len(value) if isinstance(value, str) else None,
        "component_widths_observed": sorted(widths),
        "encoding": (
            "semicolon_records_comma_components"
            if isinstance(value, str)
            else type(value).__name__
        ),
    }


def _detail_delta_pair_summary(value: Any) -> dict[str, Any]:
    """Summarize delta-time/delta-value pairs without exposing raw samples."""
    elapsed = 0
    current_value = 0
    values: list[int] = []
    offsets: list[int] = []
    valid = True
    for item in _detail_entries(value):
        if not isinstance(item, str):
            valid = False
            continue
        components = item.split(",")
        if len(components) < 2:
            valid = False
            continue
        try:
            elapsed += int(components[0] or 1)
            current_value += int(components[1])
        except ValueError:
            valid = False
            continue
        offsets.append(elapsed)
        values.append(current_value)
    decoded = bool(values) and valid
    return {
        "decoded_using_public_exporter_delta_model": decoded,
        "decoded_sample_count": len(values),
        "offset_start": offsets[0] if decoded else None,
        "offset_end": offsets[-1] if decoded else None,
        "minimum": min(values) if decoded else None,
        "maximum": max(values) if decoded else None,
    }


def _detail_text_metadata(
    value: Any, *, path: str = "$", depth: int = 0
) -> list[dict[str, Any]]:
    if depth > 5:
        return []
    found: list[dict[str, Any]] = []
    if isinstance(value, dict):
        for raw_key, item in value.items():
            key = str(raw_key)
            child_path = f"{path}.{key}"
            if key.lower() in ACTIVITY_DETAIL_TEXT_KEYS:
                found.append({
                    "path": child_path,
                    "present": item not in (None, "", [], {}),
                    "length": len(str(item)) if item not in (None, "") else 0,
                    "value_type": type(item).__name__,
                })
            if isinstance(item, (dict, list)):
                found.extend(
                    _detail_text_metadata(item, path=child_path, depth=depth + 1)
                )
    elif isinstance(value, list):
        for index, item in enumerate(value[:50]):
            found.extend(
                _detail_text_metadata(
                    item, path=f"{path}[{index}]", depth=depth + 1
                )
            )
    return found


def diagnose_activity_detail_payload(
    data: Any,
    *,
    expected_track_id: str | int,
    summary_record: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Sanitize detail.json without emitting coordinates or private text."""
    payload = data.get("data") if isinstance(data, dict) else None
    if not isinstance(payload, dict):
        return {
            "recognized_wrapper": False,
            "response_structure": _activity_shape(data),
            "detail_track_id_matches": False,
            "streams": {},
            "workout_notes": {"present": False, "matches": []},
        }

    track_id = payload.get("trackid", payload.get("trackId"))
    stream_shapes = {
        field: _detail_stream_shape(payload.get(field))
        for field in ACTIVITY_DETAIL_STREAM_FIELDS
    }
    time_values = _detail_numeric_series(payload.get("time"))
    time_encoded_count = stream_shapes["time"]["sample_count"]
    time_parse_complete = len(time_values) == time_encoded_count
    cumulative_time: list[float] = []
    elapsed = 0.0
    for value in time_values:
        elapsed += value
        cumulative_time.append(elapsed)

    altitude_values = _detail_numeric_series(payload.get("altitude"))
    altitude_encoded_count = stream_shapes["altitude"]["sample_count"]
    altitude_parse_complete = len(altitude_values) == altitude_encoded_count
    gps_count, gps_parse_complete = _detail_coordinate_sample_count(
        payload.get("longitude_latitude")
    )
    notes = _detail_text_metadata(payload)
    source_present = payload.get("source") not in (None, "")
    mapping = _activity_sport_mapping(summary_record or {})
    return {
        "recognized_wrapper": True,
        "detail_track_id": track_id,
        "detail_track_id_matches": str(track_id) == str(expected_track_id),
        "source_parameter_returned": source_present,
        "sport_mapping": mapping,
        "response_structure": _activity_shape(data),
        "safe_detail_structure": _activity_safe_nested(
            payload, include_text=False
        ),
        "streams": stream_shapes,
        "gps": {
            "gps_stream_present": gps_count > 0,
            "point_count": gps_count,
            "coordinate_record_count": stream_shapes[
                "longitude_latitude"
            ]["sample_count"],
            "coordinate_parse_complete": gps_parse_complete,
            "coordinate_values_suppressed": True,
            "timestamp_sample_count": time_encoded_count,
            "timestamp_numeric_sample_count": len(time_values),
            "timestamp_parse_complete": time_parse_complete,
            "timestamp_offset_start": (
                cumulative_time[0]
                if cumulative_time and time_parse_complete
                else None
            ),
            "timestamp_offset_end": (
                cumulative_time[-1]
                if cumulative_time and time_parse_complete
                else None
            ),
            "timestamp_semantics": (
                "delta offsets observed in public exporter and verified by "
                "bounded Z001.9 production stream probes"
            ),
        },
        "altitude": {
            "altitude_stream_present": bool(altitude_values),
            "sample_count": altitude_encoded_count,
            "numeric_sample_count": len(altitude_values),
            "numeric_parse_complete": altitude_parse_complete,
            "raw_minimum": (
                min(altitude_values)
                if altitude_values and altitude_parse_complete
                else None
            ),
            "raw_maximum": (
                max(altitude_values)
                if altitude_values and altitude_parse_complete
                else None
            ),
            "candidate_minimum_metres": (
                min(altitude_values) / 100
                if altitude_values and altitude_parse_complete
                else None
            ),
            "candidate_maximum_metres": (
                max(altitude_values) / 100
                if altitude_values and altitude_parse_complete
                else None
            ),
            "scaling_evidence": (
                "/100 production-supported by plausible Hiking, Gravel, and "
                "Ski altitude ranges; do not generalize beyond tested sports"
            ),
        },
        "heart_rate": {
            "stream_present": stream_shapes["heart_rate"]["present"],
            **_detail_delta_pair_summary(payload.get("heart_rate")),
        },
        "workout_notes": {
            "present": any(item["present"] for item in notes),
            "matches": notes,
            "text_values_suppressed": True,
        },
        "unknown_detail_field_names": sorted(
            set(str(key) for key in payload)
            - set(ACTIVITY_DETAIL_STREAM_FIELDS)
            - {"trackid", "trackId", "source", "version", "provider"}
        ),
        "evidence_level": "PRODUCTION_PROVEN",
    }


def _activity_diagnostic_window(
    from_date: str, to_date: str, timezone_name: str
) -> tuple[int, int]:
    start_day = date.fromisoformat(from_date)
    end_day = date.fromisoformat(to_date)
    if end_day < start_day:
        raise ValueError("--to-date must not be before --from-date")
    zone = ZoneInfo(timezone_name)
    start = datetime.combine(start_day, datetime.min.time(), tzinfo=zone)
    end = datetime.combine(
        end_day, datetime.max.time().replace(microsecond=0), tzinfo=zone
    )
    return int(start.timestamp()), int(end.timestamp())


def cmd_diagnose_activities(args: argparse.Namespace) -> None:
    if args.limit < 1:
        sys.exit("--limit must be at least 1")
    if args.compare_sub_data and not args.track_id:
        sys.exit("--compare-sub-data requires --track-id")
    timezone_name = args.timezone or load_config().get("timezone") or FRESHNESS_TIMEZONE
    try:
        start_track_id, stop_track_id = _activity_diagnostic_window(
            args.from_date, args.to_date, timezone_name
        )
    except (ValueError, KeyError) as exc:
        sys.exit(f"Invalid activity diagnostic date range/timezone: {exc}")
    client = _load_client()
    sports: list[dict[str, Any]] = []
    for sport in args.sport:
        try:
            if args.compare_sub_data:
                without_sub_data = client.sport_history(
                    sport, start_track_id, stop_track_id, need_sub_data=0
                )
                with_sub_data = client.sport_history(
                    sport, start_track_id, stop_track_id, need_sub_data=1
                )
                result = compare_activity_sub_data_payloads(
                    without_sub_data,
                    with_sub_data,
                    sport_segment=sport,
                    track_id=args.track_id,
                )
            else:
                payload = client.sport_history(
                    sport,
                    start_track_id,
                    stop_track_id,
                    need_sub_data=args.need_sub_data,
                )
                result = diagnose_activity_payload(
                    payload,
                    sport_segment=sport,
                    limit=args.limit,
                    include_text=args.include_text,
                    track_id=args.track_id,
                )
            result["request_status"] = "ok"
        except requests.RequestException as exc:
            result = {
                "sport_segment": sport,
                "request_status": "error",
                "error": type(exc).__name__,
            }
            if exc.response is not None:
                result["http_status"] = exc.response.status_code
        sports.append(result)
    report = {
        "endpoint_contract": {
            "method": "GET",
            "path_template": "/v1/sport/{sport}/history.json",
            "query_parameters": [
                "userid", "startTrackId", "stopTrackId", "need_sub_data", "type", "r"
            ],
            "pagination": "none_implemented",
            "server_limit_parameter": "none",
        },
        "request": {
            "from_date": args.from_date,
            "to_date": args.to_date,
            "timezone": timezone_name,
            "startTrackId": start_track_id,
            "stopTrackId": stop_track_id,
            "need_sub_data": [0, 1] if args.compare_sub_data else args.need_sub_data,
            "compare_sub_data": args.compare_sub_data,
            "output_record_limit_per_sport": args.limit,
            "track_id_filter": args.track_id,
        },
        "privacy": (
            "Credentials, user/device identifiers, secret URLs, and coordinate "
            "values are omitted. Location metadata does not imply a GPS track. Text "
            "content is omitted unless --include-text is explicit."
        ),
        "sports": sports,
    }
    _emit_json(report, args)


def cmd_diagnose_activity_detail(args: argparse.Namespace) -> None:
    timezone_name = args.timezone or load_config().get("timezone") or FRESHNESS_TIMEZONE
    try:
        start_track_id, stop_track_id = _activity_diagnostic_window(
            args.from_date, args.to_date, timezone_name
        )
    except (ValueError, KeyError) as exc:
        sys.exit(f"Invalid activity detail date range/timezone: {exc}")

    client = _load_client()
    request_status = "ok"
    diagnostic: dict[str, Any]
    source_discovered = False
    detail_request_attempted = False
    try:
        history = client.sport_history(
            "run", start_track_id, stop_track_id, need_sub_data=1
        )
        summary_record = _activity_record_by_track_id(history, args.track_id)
        if summary_record is None:
            diagnostic = {
                "history_record_found": False,
                "detail_request_attempted": False,
                "reason": "track_id_not_found_in_bounded_history_response",
            }
        else:
            source = summary_record.get("source")
            if not isinstance(source, str) or not source:
                diagnostic = {
                    "history_record_found": True,
                    "detail_request_attempted": False,
                    "reason": "history_record_has_no_usable_source_parameter",
                    "sport_mapping": _activity_sport_mapping(summary_record),
                }
            else:
                source_discovered = True
                detail_request_attempted = True
                detail = client.sport_detail(args.track_id, source)
                diagnostic = {
                    "history_record_found": True,
                    "detail_request_attempted": True,
                    **diagnose_activity_detail_payload(
                        detail,
                        expected_track_id=args.track_id,
                        summary_record=summary_record,
                    ),
                }
    except requests.RequestException as exc:
        request_status = "error"
        diagnostic = {
            "error": type(exc).__name__,
            "detail_request_contract_unverified_in_current_account": True,
            "detail_request_attempted": detail_request_attempted,
        }
        if exc.response is not None:
            diagnostic["http_status"] = exc.response.status_code

    report = {
        "endpoint_contract": {
            "method": "GET",
            "history_path": "/v1/sport/run/history.json",
            "detail_path": "/v1/sport/run/detail.json",
            "detail_query_parameters": ["trackid", "source", "r"],
            "evidence_level": "PRODUCTION_PROVEN",
            "production_verification_required": False,
        },
        "request": {
            "from_date": args.from_date,
            "to_date": args.to_date,
            "timezone": timezone_name,
            "track_id": str(args.track_id),
            "history_startTrackId": start_track_id,
            "history_stopTrackId": stop_track_id,
            "source_parameter_discovered_from_history": (
                source_discovered
            ),
        },
        "privacy": (
            "Coordinates, route values, private text, source values, credentials, "
            "headers, cookies, user/device identifiers, and URLs are omitted."
        ),
        "request_status": request_status,
        "detail": diagnostic,
    }
    _emit_json(report, args)


def cmd_diagnose_canonical_activity(args: argparse.Namespace) -> None:
    timezone_name = args.timezone or load_config().get("timezone") or FRESHNESS_TIMEZONE
    try:
        start_track_id, stop_track_id = _activity_diagnostic_window(
            args.from_date, args.to_date, timezone_name
        )
    except (ValueError, KeyError) as exc:
        sys.exit(f"Invalid canonical activity date range/timezone: {exc}")

    client = _load_client()
    try:
        history = client.sport_history(
            "run", start_track_id, stop_track_id, need_sub_data=1
        )
        summary_record = _activity_record_by_track_id(history, args.track_id)
        if summary_record is None:
            report = {
                "request_status": "ok",
                "canonical_activity_created": False,
                "reason": "track_id_not_found_in_bounded_history_response",
            }
        else:
            source = summary_record.get("source")
            if not isinstance(source, str) or not source:
                report = {
                    "request_status": "ok",
                    "canonical_activity_created": False,
                    "reason": "history_record_has_no_usable_source_parameter",
                    "sport_mapping": _activity_sport_mapping(summary_record),
                }
            else:
                detail = client.sport_detail(args.track_id, source)
                canonical = canonicalize_activity(
                    summary_record,
                    detail,
                    timezone_name=timezone_name,
                )
                report = {
                    "request_status": "ok",
                    "canonical_activity_created": True,
                    "endpoint_contract": {
                        "history": "/v1/sport/run/history.json",
                        "detail": "/v1/sport/run/detail.json",
                    },
                    "request": {
                        "from_date": args.from_date,
                        "to_date": args.to_date,
                        "timezone": timezone_name,
                        "track_id": str(args.track_id),
                    },
                    "canonical_activity": safe_canonical_activity(canonical),
                }
    except requests.RequestException as exc:
        report = {
            "request_status": "error",
            "canonical_activity_created": False,
            "error": type(exc).__name__,
        }
        if exc.response is not None:
            report["http_status"] = exc.response.status_code
    report["privacy"] = (
        "Coordinates, sample values, notes text, source values, credentials, "
        "headers, cookies, user/device identifiers, URLs, and raw payloads omitted."
    )
    _emit_json(report, args)


def _activity_failure_category(exc: Exception) -> str:
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        if exc.response.status_code in (401, 403):
            return "authentication"
        if exc.response.status_code == 429:
            return "rate_limit"
        return "network"
    if isinstance(exc, requests.RequestException):
        return "network"
    if isinstance(exc, sqlite3.DatabaseError):
        return "database"
    if isinstance(exc, (ValueError, TypeError, KeyError)):
        return "payload_or_normalization"
    return "unknown"


def sync_native_activities(
    client: ZeppClient,
    database: Database,
    from_date: str,
    to_date: str,
    *,
    timezone_name: str = FRESHNESS_TIMEZONE,
    refresh_details: bool = False,
    max_activities: int = 50,
) -> dict[str, Any]:
    """Bounded one-page incremental activity sync with atomic per-activity writes."""
    if max_activities < 1:
        raise ValueError("max_activities must be at least 1")
    start_track_id, stop_track_id = _activity_diagnostic_window(
        from_date, to_date, timezone_name
    )
    database.mark_running_activity_syncs_interrupted()
    run_id = database.start_activity_sync(from_date, to_date, timezone_name)
    result: dict[str, Any] = {
        "status": "running",
        "database_path": str(database.path),
        "from_date": from_date,
        "to_date": to_date,
        "timezone": timezone_name,
        "activities_seen": 0,
        "activities_processed": 0,
        "inserted": 0,
        "updated": 0,
        "unchanged": 0,
        "detail_fetch_success": 0,
        "detail_fetch_failed": 0,
        "detail_fetch_skipped": 0,
        "history_next": None,
        "pagination_complete": False,
        "truncated_by_max_activities": False,
        "failures": [],
    }
    try:
        history = client.sport_history(
            "run", start_track_id, stop_track_id, need_sub_data=1
        )
        records = _activity_records(history)
        result["activities_seen"] = len(records)
        next_cursor = _activity_response_next(history)
        result["history_next"] = next_cursor
        result["pagination_complete"] = str(next_cursor) == "-1"
        ordered = sorted(
            records,
            key=lambda record: _activity_number(
                record.get("trackid", record.get("trackId"))
            ) or 0,
            reverse=True,
        )
        if len(ordered) > max_activities:
            result["truncated_by_max_activities"] = True
            ordered = ordered[:max_activities]

        for history_record in ordered:
            track_id = history_record.get(
                "trackid", history_record.get("trackId")
            )
            if track_id in (None, ""):
                result["detail_fetch_failed"] += 1
                result["failures"].append({
                    "track_id": None,
                    "category": "payload_or_normalization",
                    "error_type": "MissingTrackId",
                })
                continue
            state = database.activity_sync_state(track_id, history_record)
            if state == "unchanged" and not refresh_details:
                database.touch_activity(track_id)
                result["unchanged"] += 1
                result["detail_fetch_skipped"] += 1
                result["activities_processed"] += 1
                continue
            source = history_record.get("source")
            if not isinstance(source, str) or not source:
                result["detail_fetch_failed"] += 1
                result["failures"].append({
                    "track_id": str(track_id),
                    "category": "payload_or_normalization",
                    "error_type": "MissingSourceParameter",
                })
                continue
            try:
                detail = client.sport_detail(track_id, source)
                result["detail_fetch_success"] += 1
                canonical = canonicalize_activity(
                    history_record,
                    detail,
                    timezone_name=timezone_name,
                )
                flags = canonical.get("quality", {}).get("flags", [])
                if (
                    "HISTORY_DETAIL_TRACK_ID_MISMATCH" in flags
                    or "DETAIL_TRACK_ID_MISSING" in flags
                    or "DETAIL_WRAPPER_UNRECOGNIZED" in flags
                ):
                    raise ValueError("detail identity or wrapper validation failed")
                stored = database.store_canonical_activity(
                    canonical, history_record, detail
                )
                result[stored] += 1
                result["activities_processed"] += 1
            except Exception as exc:
                result["detail_fetch_failed"] += 1
                result["failures"].append({
                    "track_id": str(track_id),
                    "category": _activity_failure_category(exc),
                    "error_type": type(exc).__name__,
                })

        incomplete = (
            result["detail_fetch_failed"] > 0
            or not result["pagination_complete"]
            or result["truncated_by_max_activities"]
        )
        result["status"] = "partial" if incomplete else "ok"
        if not result["pagination_complete"]:
            result["pagination_note"] = (
                "data.next was not terminal; no guessed cursor traversal performed"
            )
    except Exception as exc:
        result["status"] = "error"
        result["error_category"] = _activity_failure_category(exc)
        result["error_type"] = type(exc).__name__
    finally:
        database.finish_activity_sync(run_id, result)
    return result


def _activity_sync_dates(args: argparse.Namespace) -> tuple[str, str]:
    if args.days is not None and (args.from_date or args.to_date):
        raise ValueError("--days cannot be combined with explicit dates")
    if bool(args.from_date) != bool(args.to_date):
        raise ValueError("--from-date and --to-date must be provided together")
    if args.from_date:
        start = date.fromisoformat(args.from_date)
        end = date.fromisoformat(args.to_date)
        if end < start:
            raise ValueError("--to-date must not be before --from-date")
        return start.isoformat(), end.isoformat()
    days = args.days if args.days is not None else 7
    if days < 1:
        raise ValueError("--days must be at least 1")
    timezone_name = args.timezone or load_config().get("timezone") or FRESHNESS_TIMEZONE
    today = datetime.now(ZoneInfo(timezone_name)).date()
    return (today - timedelta(days=days - 1)).isoformat(), today.isoformat()


def cmd_sync_activities(args: argparse.Namespace) -> None:
    lock = SyncLock(args.lock_path) if args.lock_path else None
    if lock is not None and not lock.acquire(nonblocking=True):
        _emit_json(
            {
                "status": "skipped",
                "reason": "lock_held",
                "lock_path": str(args.lock_path),
            },
            args,
        )
        return
    try:
        try:
            from_date, to_date = _activity_sync_dates(args)
            timezone_name = (
                args.timezone
                or load_config().get("timezone")
                or FRESHNESS_TIMEZONE
            )
            database = Database(_db_path_from_args(args))
            try:
                result = sync_native_activities(
                    _load_client(),
                    database,
                    from_date,
                    to_date,
                    timezone_name=timezone_name,
                    refresh_details=args.refresh_details,
                    max_activities=args.max_activities,
                )
            finally:
                database.close()
        except (ValueError, KeyError) as exc:
            raise SystemExit(f"Invalid activity sync options: {exc}") from None
    finally:
        if lock is not None:
            lock.release()
    _emit_json(result, args)
    if result["status"] == "error":
        raise SystemExit(2)


def cmd_activity_status(args: argparse.Namespace) -> None:
    database = Database(_db_path_from_args(args))
    try:
        result = database.activity_status()
    finally:
        database.close()
    _emit_json(result, args)


def cmd_inspect_activity(args: argparse.Namespace) -> None:
    database = Database(_db_path_from_args(args))
    try:
        result = database.inspect_activity(
            args.track_id, include_notes=args.include_notes
        )
    finally:
        database.close()
    if result is None:
        raise SystemExit("Activity not found")
    _emit_json(result, args)


def cmd_diagnose_sport_coverage(args: argparse.Namespace) -> None:
    if args.mapping_list and args.json:
        sys.exit("--mapping-list cannot be combined with --json")
    timezone_name = args.timezone or load_config().get("timezone") or FRESHNESS_TIMEZONE
    try:
        start_track_id, stop_track_id = _activity_diagnostic_window(
            args.from_date, args.to_date, timezone_name
        )
    except (ValueError, KeyError) as exc:
        sys.exit(f"Invalid sport coverage date range/timezone: {exc}")
    client = _load_client()
    try:
        payload = client.sport_history(
            "run", start_track_id, stop_track_id, need_sub_data=args.need_sub_data
        )
        inventory = inventory_activity_payload(
            payload, sport_segment="run", timezone_name=timezone_name
        )
        request_status = "ok"
    except requests.RequestException as exc:
        inventory = {
            "sport_segment": "run",
            "raw_record_count": 0,
            "type_group_count": 0,
            "observed_type_ids": [],
            "type_groups": [],
            "error": type(exc).__name__,
        }
        if exc.response is not None:
            inventory["http_status"] = exc.response.status_code
        request_status = "error"
    report = {
        "endpoint_contract": {
            "method": "GET",
            "path": "/v1/sport/run/history.json",
            "role": (
                "proven broader than literal runs; all-sport completeness remains "
                "unproven"
            ),
            "pagination": "not_followed_unproven",
        },
        "request": {
            "from_date": args.from_date,
            "to_date": args.to_date,
            "timezone": timezone_name,
            "startTrackId": start_track_id,
            "stopTrackId": stop_track_id,
            "need_sub_data": args.need_sub_data,
        },
        "privacy": (
            "Grouped summaries only. Credentials, user/device identifiers, URLs, "
            "coordinates, and activity text values are omitted."
        ),
        "request_status": request_status,
        "inventory": inventory,
    }
    if args.mapping_list:
        if request_status != "ok":
            print(f"sport coverage request failed: {inventory.get('error', 'unknown')}")
        else:
            print(format_sport_coverage_mapping_list(inventory))
    else:
        _emit_json(report, args)


def cmd_diagnose_sport_capabilities(args: argparse.Namespace) -> None:
    timezone_name = args.timezone or load_config().get("timezone") or FRESHNESS_TIMEZONE
    try:
        start_track_id, stop_track_id = _activity_diagnostic_window(
            args.from_date, args.to_date, timezone_name
        )
    except (ValueError, KeyError) as exc:
        sys.exit(f"Invalid sport capability date range/timezone: {exc}")
    client = _load_client()
    try:
        payload = client.sport_history(
            "run", start_track_id, stop_track_id, need_sub_data=args.need_sub_data
        )
        audit = audit_activity_capabilities(
            payload, timezone_name=timezone_name
        )
        request_status = "ok"
    except requests.RequestException as exc:
        audit = {
            "requested_fixture_count": len(ACTIVITY_CAPABILITY_FIXTURES),
            "matched_fixture_count": 0,
            "activities": [],
            "error": type(exc).__name__,
        }
        if exc.response is not None:
            audit["http_status"] = exc.response.status_code
        request_status = "error"
    report = {
        "endpoint_contract": {
            "method": "GET",
            "path": "/v1/sport/run/history.json",
            "pagination": "not_followed_unproven",
        },
        "request": {
            "from_date": args.from_date,
            "to_date": args.to_date,
            "timezone": timezone_name,
            "startTrackId": start_track_id,
            "stopTrackId": stop_track_id,
            "need_sub_data": args.need_sub_data,
        },
        "privacy": (
            "Only the 14 approved representative activity IDs are audited. "
            "Credentials, user/device identifiers, URLs, coordinates, and "
            "activity text values are omitted."
        ),
        "request_status": request_status,
        "audit": audit,
    }
    _emit_json(report, args)


def cmd_band_data(args: argparse.Namespace) -> None:
    c = _load_client()
    if args.from_date and args.to_date:
        start = date.fromisoformat(args.from_date)
        end = date.fromisoformat(args.to_date)
    else:
        end = _today_utc()
        start = end - timedelta(days=args.days - 1)
    data = c.band_data(start, end, query_type=args.query_type)
    _emit_json(data, args)


def cmd_manual_data(args: argparse.Namespace) -> None:
    c = _load_client()
    data = c.manual_data(args.type)
    _emit_json(data, args)


def cmd_user_info(args: argparse.Namespace) -> None:
    c = _load_client()
    data = c.get_user_info()
    _emit_json(data, args)


def cmd_blood_pressure(args: argparse.Namespace) -> None:
    c = _load_client()
    to_d = date.fromisoformat(args.to_date) if args.to_date else None
    data = c.blood_pressure_me(days=args.bp_days, to_date=to_d)
    _emit_json(data, args)


# /users/{id}/events — different from /v2/users/me/events (watch-centric stream).
USER_EVENT_PRESETS: dict[str, tuple[str, str | None]] = {
    "all-day-stress": ("all_day_stress", None),
    "pai": ("PaiHealthInfo", None),
    "spo2": ("blood_oxygen", "click"),
    "single-stress": ("single_stress", None),
    # subType seen in proxy captures for this stream:
    "health-data": ("health_data", "blood_pressure"),
}

# /users/{id}/events/dateString — ISO window + timezone.
USER_EVENT_DAY_PRESETS: dict[str, tuple[str, str]] = {
    "spo2-odi": ("blood_oxygen", "odi"),
    "spo2-osa": ("blood_oxygen", "osa_event"),
}


def cmd_user_events(args: argparse.Namespace) -> None:
    c = _load_client()
    from_ms, to_ms = _ms_window(args.days)
    et = args.type
    st: str | None = args.subtype
    if args.preset:
        et, pst = USER_EVENT_PRESETS[args.preset]
        st = pst
    if not et:
        sys.exit("Provide --preset or --type (and --subtype if required).")
    data = c.events_user(
        et, from_ms, to_ms, sub_type=st, limit=args.limit, reverse=args.reverse
    )
    _emit_json(data, args)


def cmd_user_events_day(args: argparse.Namespace) -> None:
    c = _load_client()
    et = args.type
    st = args.subtype
    if args.preset:
        et, st = USER_EVENT_DAY_PRESETS[args.preset]
    if not et or not st:
        sys.exit("Provide --preset or both --type and --subtype.")
    tz = args.timezone or load_config().get("timezone") or "UTC"
    data = c.events_user_date_string(
        et,
        st,
        args.start,
        args.end,
        tz=tz,
        limit=args.limit,
        reverse=args.reverse,
    )
    _emit_json(data, args)


def cmd_second_hr(args: argparse.Namespace) -> None:
    c = _load_client()
    from_ms, to_ms = _ms_window(args.days)
    data = c.file_info_events(
        "second_heart_rate",
        "real_data",
        from_ms,
        to_ms,
        limit=args.limit,
    )
    _emit_json(data, args)


_EVENT_PRESETS: dict[str, tuple[str, str]] = {
    "temperature": ("readiness", "watch_score"),
    "readiness": ("readiness", "watch_score"),
    "daily-health": ("DailyHealth", "summary"),
    "body-battery": ("Charge", "real_data"),
    "stress": ("Charge", "stress_data"),
    "hrv": ("hrv_sdnn", "real_data"),
    "hrv-rmssd": ("HRVRMSSD", "real_data"),
    "respiratory": ("RespiratoryRate", "real_data"),
    "blood-pressure": ("blood_pressure", "real_data"),
    "emotion": ("Emotion", "real_data"),
    "lactate-threshold": ("LactateThreshold", "summary"),
}

EVENT_DOMAIN_CANDIDATES: tuple[tuple[str, str], ...] = (
    ("Charge", "real_data"),
    ("Charge", "wake_data"),
    ("Charge", "insight_data"),
    ("Charge", "summary"),
    ("HRVRMSSD", "real_data"),
    ("exertion", "algo_result"),
    ("LifeLoad", "summary"),
    ("readiness", "watch_score"),
    ("DailyHealth", "summary"),
    ("BioCharge", "summary"),
    ("BioCharge", "real_data"),
    ("recovery", "summary"),
    ("recovery", "real_data"),
    ("sleep", "summary"),
    ("sleep", "score"),
    ("sleep", "sleep_score"),
    ("sleep", "real_data"),
)


def _ms_window(days: int) -> tuple[int, int]:
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    return int(start.timestamp() * 1000), int(end.timestamp() * 1000)


def _emit_json(data: Any, args: argparse.Namespace) -> None:
    """Print JSON; --json uses compact one-line output (good for jq / scripts)."""
    if getattr(args, "json", False):
        print(json.dumps(data, ensure_ascii=False, separators=(",", ":")))
    else:
        print(json.dumps(data, indent=2, ensure_ascii=False))


def cmd_events(args: argparse.Namespace) -> None:
    c = _load_client()
    from_ms, to_ms = _ms_window(args.days)
    data = c.events(args.type, args.subtype, from_ms, to_ms, limit=args.limit)
    _emit_json(data, args)


def discover_event_domains(
    fetch: Any,
    from_ms: int,
    to_ms: int,
    candidates: tuple[tuple[str, str], ...] = EVENT_DOMAIN_CANDIDATES,
) -> list[dict[str, Any]]:
    """Probe known candidate domains; this is discovery, not a semantic map."""
    found: list[dict[str, Any]] = []
    for event_type, sub_type in candidates:
        try:
            data = fetch(event_type, sub_type, from_ms, to_ms)
            count = len(_event_records(data))
            found.append({
                "eventType": event_type,
                "subType": sub_type,
                "item_count": count,
                "status": "nonempty" if count else "empty",
                "source": "zepp",
            })
        except requests.RequestException:
            found.append({
                "eventType": event_type,
                "subType": sub_type,
                "item_count": None,
                "status": "request_error",
                "source": "zepp",
            })
    return found


def cmd_event_domains(args: argparse.Namespace) -> None:
    c = _load_client()
    from_ms, to_ms = _ms_window(args.days)
    domains = discover_event_domains(
        lambda event_type, sub_type, start, end: c.events(
            event_type, sub_type, start, end, limit=args.limit
        ),
        from_ms,
        to_ms,
    )
    if args.json:
        _emit_json(domains, args)
        return
    print(f"Candidate Zepp event domains ({args.days} days)\n")
    for domain in domains:
        print(
            f"{domain['eventType']} / {domain['subType']}: "
            f"{domain['status']} ({domain['item_count'] if domain['item_count'] is not None else '—'} items)"
        )
    print("\nThis probes known candidates only; it is not an exhaustive server-side domain listing.")


def _first_value(mapping: dict[str, Any], *names: str, default: Any = None) -> Any:
    for name in names:
        if name in mapping:
            return mapping[name]
    return default


def _as_number(value: Any) -> Any:
    """Keep numeric API values numeric, while tolerating absent/empty fields."""
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    try:
        text = str(value).strip()
        number = float(text)
        return int(number) if number.is_integer() else number
    except (TypeError, ValueError):
        return value


def _event_records(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if not isinstance(data, dict):
        return []
    for key in ("items", "data", "records", "result"):
        value = data.get(key)
        if isinstance(value, list):
            return [x for x in value if isinstance(x, dict)]
        if isinstance(value, dict):
            nested = _event_records(value)
            if nested:
                return nested
    return []


def _parse_json_extra(value: Any) -> tuple[Any, str | None]:
    if value in (None, ""):
        return None, None
    if isinstance(value, (dict, list)):
        return value, None
    if not isinstance(value, str):
        return value, None
    try:
        return json.loads(value), None
    except (TypeError, json.JSONDecodeError) as exc:
        return value, str(exc)


def _format_offset(value: Any) -> str:
    number = _as_number(value)
    if not isinstance(number, (int, float)):
        return "—"
    total_seconds = max(0, int(number) // 1000)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _record_timezone(record: dict[str, Any]) -> str | None:
    value = _first_value(record, "timezone", "timeZone", "tz")
    return str(value) if value else None


def _local_datetime(timestamp_ms: Any, timezone_name: str | None) -> datetime | None:
    timestamp = _as_number(timestamp_ms)
    if not isinstance(timestamp, (int, float)):
        return None
    try:
        tz = ZoneInfo(timezone_name) if timezone_name else timezone.utc
    except (KeyError, ValueError):
        tz = timezone.utc
    return datetime.fromtimestamp(timestamp / 1000, tz=tz)


def normalize_insight_data(data: Any) -> list[dict[str, Any]]:
    """Normalize Charge/insight_data daily records without assigning meanings."""
    normalized: list[dict[str, Any]] = []
    for record in _event_records(data):
        value = record.get("value") if isinstance(record.get("value"), dict) else {}
        source = {**value, **record}
        samples = _first_value(source, "samples", "sample", default=[])
        if isinstance(samples, dict):
            samples = [samples]
        if not isinstance(samples, list):
            samples = []
        timestamp = _as_number(_first_value(source, "timestamp", "time", "ts"))
        start_time = _as_number(_first_value(source, "startTime", "start_time", "start"))
        timezone_name = _record_timezone(source)
        local_date = _local_datetime(timestamp, timezone_name)
        day = _first_value(source, "date", "day", "dayId")
        if day is None and local_date:
            day = local_date.date().isoformat()
        daily: dict[str, Any] = {
            "date": str(day) if day is not None else None,
            "timestamp": timestamp,
            "start_time": start_time,
            "timezone": timezone_name,
            "device_id": _first_value(source, "deviceId", "device_id"),
            "device_type": _first_value(source, "deviceType", "device_type"),
            "eventType": "Charge",
            "subType": "insight_data",
            "event_type": "Charge",
            "sub_type": "insight_data",
            "source": "zepp",
            "calculation_source": "zepp",
            "mapping_confidence": "unknown",
            "samples": [],
        }
        for raw_sample in samples:
            if not isinstance(raw_sample, dict):
                continue
            extra, extra_error = _parse_json_extra(
                _first_value(raw_sample, "jsonExtra", "json_extra")
            )
            sample: dict[str, Any] = {
                "insight_id": _as_number(_first_value(raw_sample, "insightId", "insight_id")),
                "insight": _as_number(raw_sample.get("insight")),
                "type": _as_number(raw_sample.get("type")),
                "diff": _as_number(raw_sample.get("diff")),
                "slope": _as_number(raw_sample.get("slope")),
                "start_offset_ms": _as_number(_first_value(raw_sample, "s", "start_offset_ms")),
                "end_offset_ms": _as_number(_first_value(raw_sample, "e", "end_offset_ms")),
                "track_id": _as_number(_first_value(raw_sample, "trackId", "track_id")),
                "threshold": _as_number(_first_value(raw_sample, "thres", "threshold")),
                "u": _as_number(raw_sample.get("u")),
                "json_extra": extra,
                "parsed_json_extra": extra,
            }
            sample["raw_u"] = sample["u"]
            if extra_error:
                sample["json_extra_error"] = extra_error
            daily["samples"].append(sample)
        normalized.append(daily)
    return normalized


def _insight_rows(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for record in records:
        for sample in record["samples"]:
            rows.append({
                "date": record["date"],
                "timestamp": record.get("timestamp"),
                "start_time": record.get("start_time"),
                **sample,
            })
    return rows


def _print_insight_output(records: list[dict[str, Any]], days: int) -> None:
    rows = _insight_rows(records)
    print(f"Insight data ({days} days)\n")
    print(f"{'Date':<12} {'Type':>4} {'Insight':>7} {'Diff':>6} {'Slope':>12} {'Start':>8} {'End':>8} {'Track ID':>12}")
    for row in rows:
        print(
            f"{str(row['date'] or '—'):<12} {str(row['type'] if row['type'] is not None else '—'):>4} "
            f"{str(row['insight'] if row['insight'] is not None else '—'):>7} "
            f"{str(row['diff'] if row['diff'] is not None else '—'):>6} "
            f"{str(row['slope'] if row['slope'] is not None else '—'):>12} "
            f"{_format_offset(row['start_offset_ms']):>8} {_format_offset(row['end_offset_ms']):>8} "
            f"{str(row['track_id'] if row['track_id'] is not None else '—'):>12}"
        )
    type_counts = Counter(str(row["type"]) for row in rows if row["type"] is not None)
    insight_counts = Counter(str(row["insight"]) for row in rows if row["insight"] is not None)
    def sort_code(value: str) -> tuple[int, Any]:
        try:
            return (0, int(value))
        except ValueError:
            return (1, value)

    print("\nUnique insight values: " + ", ".join(sorted(insight_counts, key=sort_code)))
    print("Unique type values: " + ", ".join(sorted(type_counts, key=sort_code)))
    print("\nCounts by type:")
    for key in sorted(type_counts, key=sort_code):
        print(f"type={key}: {type_counts[key]}")
    print("Counts by insight:")
    for key in sorted(insight_counts, key=sort_code):
        print(f"insight={key}: {insight_counts[key]}")


def _write_insight_csv(path: str, records: list[dict[str, Any]]) -> None:
    fields = ["date", "timestamp", "start_time", "timezone", "device_id", "device_type",
              "insight_id", "insight", "type", "diff", "slope", "start_offset_ms",
              "end_offset_ms", "track_id", "threshold", "u", "mins_below_threshold",
              "diff_below_threshold"]
    with Path(path).expanduser().open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for record in records:
            for sample in record["samples"]:
                extra = sample.get("json_extra") if isinstance(sample.get("json_extra"), dict) else {}
                writer.writerow({
                    **{key: record.get(key) for key in fields},
                    **{key: sample[key] for key in fields if key in sample},
                    "mins_below_threshold": extra.get("minsBelowThreshold"),
                    "diff_below_threshold": extra.get("diffBelowThreshold"),
                })


def cmd_insights(args: argparse.Namespace) -> None:
    if args.csv and args.json:
        sys.exit("Use either --json or --csv, not both.")
    c = _load_client()
    from_ms, to_ms = _ms_window(args.days)
    records = normalize_insight_data(c.events("Charge", "insight_data", from_ms, to_ms, limit=args.limit))
    if args.csv:
        _write_insight_csv(args.csv, records)
        print(f"Wrote {args.csv} ({len(_insight_rows(records))} samples)")
    elif args.json:
        _emit_json(records, args)
    else:
        _print_insight_output(records, args.days)


def _record_payload(record: dict[str, Any]) -> dict[str, Any]:
    value = record.get("value")
    payload = dict(value) if isinstance(value, dict) else {}
    payload.update({k: v for k, v in record.items() if k != "value"})
    return payload


def _record_timestamp(record: dict[str, Any], payload: dict[str, Any]) -> Any:
    return _as_number(_first_value(payload, "timestamp", "time", "ts", "startTime", "start_time"))


def _record_date(record: dict[str, Any], payload: dict[str, Any]) -> str | None:
    day = _first_value(payload, "date", "day", "dayId")
    if day is not None:
        return str(day)
    timestamp = _record_timestamp(record, payload)
    local = _local_datetime(timestamp, _record_timezone(payload))
    return local.date().isoformat() if local else None


def _provenance(event_type: str, sub_type: str, confidence: str) -> dict[str, str]:
    return {
        "eventType": event_type,
        "subType": sub_type,
        "event_type": event_type,
        "sub_type": sub_type,
        "source": "zepp",
        "calculation_source": "zepp",
        "mapping_confidence": confidence,
    }


def _normalize_value_records(
    data: Any,
    event_type: str,
    sub_type: str,
    fields: tuple[str, ...],
    *,
    confidence: str = "confirmed",
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    provenance = _provenance(event_type, sub_type, confidence)
    for record in _event_records(data):
        payload = _record_payload(record)
        item: dict[str, Any] = {
            "date": _record_date(record, payload),
            "timestamp": _record_timestamp(record, payload),
            "start_time": _as_number(_first_value(payload, "startTime", "start_time", "start")),
            **provenance,
        }
        for field in fields:
            if field in payload:
                item[field] = payload[field]
        item["raw_value"] = payload
        normalized.append(item)
    return normalized


def _sample_source(record: dict[str, Any], payload: dict[str, Any]) -> list[dict[str, Any]]:
    samples = _first_value(payload, "samples", "sample", default=[])
    if isinstance(samples, dict):
        return [samples]
    if isinstance(samples, list):
        return [sample for sample in samples if isinstance(sample, dict)]
    return []


def _normalize_sample_records(
    data: Any,
    event_type: str,
    sub_type: str,
    fields: tuple[str, ...],
    *,
    confidence: str = "candidate",
    sample_date_resolver: Any = None,
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    provenance = _provenance(event_type, sub_type, confidence)
    for record in _event_records(data):
        payload = _record_payload(record)
        parent_timestamp = _record_timestamp(record, payload)
        parent_start = _as_number(_first_value(payload, "startTime", "start_time", "start"))
        parent_date = _record_date(record, payload)
        samples = _sample_source(record, payload)
        if not samples and any(field in payload for field in fields):
            samples = [payload]
        for sample in samples:
            offset = _as_number(_first_value(sample, "s", "start_offset_ms"))
            sample_timestamp = (
                parent_start + offset
                if isinstance(parent_start, (int, float)) and isinstance(offset, (int, float))
                else parent_timestamp
            )
            event_date = (
                sample_date_resolver(
                    record, payload, sample, parent_date, sample_timestamp
                )
                if sample_date_resolver
                else parent_date
            )
            item: dict[str, Any] = {
                "date": event_date,
                "timestamp": parent_timestamp,
                "start_time": parent_start,
                "sample_timestamp": sample_timestamp,
                **provenance,
            }
            for field in fields:
                if field in sample:
                    item[field] = sample[field]
            item["raw_sample"] = sample
            normalized.append(item)
    return normalized


def normalize_hrv_data(data: Any) -> list[dict[str, Any]]:
    rows = _normalize_sample_records(
        data,
        "HRVRMSSD",
        "real_data",
        ("hrv", "s", "u"),
        confidence="confirmed",
    )
    for row in rows:
        row["offset"] = row.get("s")
        row["raw_u"] = row.get("u")
        when = _local_datetime(row.get("sample_timestamp"), None)
        row["time"] = when.strftime("%H:%M:%S") if when else None
    return rows


def normalize_charge_data(data: Any) -> list[dict[str, Any]]:
    rows = _normalize_sample_records(
        data,
        "Charge",
        "real_data",
        ("total", "physical", "mental", "s", "e"),
        confidence="candidate",
    )
    for row in rows:
        row["start_offset_ms"] = row.get("s")
        row["end_offset_ms"] = row.get("e")
    return rows


WAKE_FIELDS = (
    "bioChargeWake",
    "wakeCharge",
    "physicalWake",
    "mentalWake",
    "dailyFitnessScore",
    "stressFitnessScore",
    "exertionScore",
    "chronicWeightDaily",
    "avgImpactOnHybridCharge",
    "noWearParams",
    "snapshot",
    "u",
    "s",
)


READINESS_FIELDS = (
    "status",
    "hrvScore",
    "hrvInsight",
    "hrvBaseline",
    "sleepHRV",
    "rhrScore",
    "rhrInsight",
    "rhrBaseline",
    "sleepRHR",
    "phyScore",
    "phyInsight",
    "phyBaseline",
    "mentScore",
    "mentInsight",
    "mentBaseLine",
    "skinTempScore",
    "skinTempInsight",
    "skinTempBaseLine",
    "skinTempCalibrated",
    "ahiScore",
    "ahiInsight",
    "ahiBaseline",
    "rdnsScore",
    "rdnsInsight",
    "afibScore",
    "afibInsight",
    "timestampUpdate",
    "insightId",
)



def _int_or_none(value):
    """Return a canonical integer for Zepp numeric fields, or None."""
    if value is None or value == "":
        return None

    if isinstance(value, bool):
        return None

    try:
        return int(value)
    except (TypeError, ValueError):
        return None


FOOD_MEAL_TYPES = {
    1: "Breakfast",
    2: "Morning Snack",
    3: "Lunch",
    4: "Afternoon Snack",
    5: "Dinner",
    6: "Evening Snack",
}


def _food_number(value: Any) -> int | float | None:
    """Canonicalize a native Food number without inventing zero."""
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return int(number) if number.is_integer() else number


def food_meal_label(meal_type: Any) -> str | None:
    """Return an audited label while keeping unknown values representable."""
    canonical = _food_number(meal_type)
    if isinstance(canonical, int):
        return FOOD_MEAL_TYPES.get(canonical, f"Unknown ({canonical})")
    if meal_type not in (None, ""):
        return f"Unknown ({meal_type})"
    return None


def _food_code(value: Any) -> Any:
    number = _food_number(value)
    return number if number is not None else value


def normalize_food_data(payload: Any) -> list[dict[str, Any]]:
    """Normalize production-validated native Food/real_data entries."""
    normalized: list[dict[str, Any]] = []
    for record in _event_records(payload):
        if not isinstance(record, dict):
            continue
        value = record.get("value")
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except (TypeError, json.JSONDecodeError):
                value = {}
        source = dict(value) if isinstance(value, dict) else {}
        source.update({key: item for key, item in record.items() if key != "value"})
        event_type = source.get("eventType")
        sub_type = source.get("subType")
        if event_type not in (None, "Food"):
            continue
        if sub_type not in (None, "real_data"):
            continue

        food_log_id = source.get("foodLogId")
        if food_log_id in (None, ""):
            continue
        meal_type_raw = source.get("mealType")
        meal_type_number = _food_number(meal_type_raw)
        meal_type = (
            meal_type_number
            if meal_type_number is not None
            else meal_type_raw
        )
        timestamp = _record_timestamp(record, source)
        normalized.append({
            "food_log_id": str(food_log_id),
            "date": _record_date(record, source),
            "timestamp_ms": (
                int(timestamp)
                if isinstance(timestamp, (int, float))
                else None
            ),
            "meal_type": meal_type,
            "meal_label": food_meal_label(meal_type),
            "meal_name": source.get("mealName"),
            "food_name": source.get("foodName"),
            "measure_weight": _food_number(source.get("measureWeight")),
            "weight_unit": source.get("weightUnit"),
            "energy": _food_number(source.get("energy")),
            "carbohydrates": _food_number(source.get("carbohydrates")),
            "protein": _food_number(source.get("protein")),
            "fat_total": _food_number(source.get("fatTotal")),
            "fiber": _food_number(source.get("fiber")),
            "servings": _food_number(source.get("servings")),
            "labels": source.get("labels"),
            "emoji": source.get("emoji"),
            "recognize_type": _food_code(source.get("recognizeType")),
            "recognize_source_type": _food_code(
                source.get("recognizeSourceType")
            ),
            "provenance": _provenance("Food", "real_data", "confirmed"),
            "raw": record,
        })
    return normalized


def normalize_sport_load_data(payload: Any) -> list[dict[str, Any]]:
    """Normalize factual WatchSportStatistics/SPORT_LOAD daily rows."""
    if not isinstance(payload, dict):
        return []
    items = payload.get("items")
    if not isinstance(items, list):
        return []
    rows: list[dict[str, Any]] = []
    for native in items:
        if not isinstance(native, dict):
            continue
        event_date = native.get("dayId")
        if not isinstance(event_date, str):
            continue
        try:
            date.fromisoformat(event_date)
        except ValueError:
            continue
        rows.append({
            "event_date": event_date,
            "generated_time_s": _int_or_none(native.get("generatedTime")),
            "updated_time_ms": _int_or_none(native.get("updateTime")),
            "current_day_training_load": _int_or_none(
                native.get("currnetDayTrainLoad")
            ),
            "wtl_sum": _int_or_none(native.get("wtlSum")),
            "optimal_min": _int_or_none(native.get("wtlSumOptimalMin")),
            "optimal_max": _int_or_none(native.get("wtlSumOptimalMax")),
            "overreaching_threshold": _int_or_none(
                native.get("wtlSumOverreaching")
            ),
            "device_source": _int_or_none(native.get("device_source")),
            "provenance": {
                "method": "GET",
                "path": "/v2/watch/users/{id}/WatchSportStatistics/SPORT_LOAD",
                "confidence": "production_confirmed",
            },
            "raw": native,
        })
    return rows


def normalize_stress_data(payload):
    """Normalize native Zepp ``all_day_stress`` events.

    Returns one factual daily record per event. The native daily aggregates
    remain authoritative. The 5-minute timeline is preserved as a sparse
    sample list; missing intervals are not synthesized.

    Supports both known readback shapes:
    - /users/{id}/events flattened records
    - /v2/users/me/events records with a nested ``value`` object
    """
    normalized = []

    if payload is None:
        return normalized

    # Reuse the project's generic event extraction when available.
    try:
        records = _event_records(payload)
    except NameError:
        if isinstance(payload, list):
            records = payload
        elif isinstance(payload, dict):
            records = (
                payload.get("items")
                or payload.get("data")
                or payload.get("events")
                or []
            )
            if isinstance(records, dict):
                records = [records]
        else:
            records = []

    for record in records:
        if not isinstance(record, dict):
            continue

        try:
            value = _record_payload(record)
        except NameError:
            value = record.get("value") or record

        if not isinstance(value, dict):
            continue

        event_type = (
            record.get("eventType")
            or record.get("type")
            or value.get("eventType")
        )

        # Generic /users event output may already be type-filtered and omit
        # eventType. Reject only an explicit non-Stress type.
        if event_type not in (None, "all_day_stress"):
            continue

        raw_samples = value.get("data")

        # Some event paths preserve data as a JSON string.
        if isinstance(raw_samples, str):
            try:
                import json as _json
                raw_samples = _json.loads(raw_samples)
            except Exception:
                raw_samples = []

        if not isinstance(raw_samples, list):
            raw_samples = []

        samples = []

        for sample in raw_samples:
            if not isinstance(sample, dict):
                continue

            timestamp_ms = sample.get("time")
            stress = sample.get("value")

            if not isinstance(timestamp_ms, (int, float)):
                continue

            if not isinstance(stress, (int, float)):
                continue

            # Stress is a native 0..100 score. Do not invent or interpolate
            # missing five-minute intervals.
            if stress < 0 or stress > 100:
                continue

            if stress <= 39:
                category = "relaxed"
            elif stress <= 59:
                category = "normal"
            elif stress <= 79:
                category = "medium"
            else:
                category = "high"

            samples.append(
                {
                    "timestamp_ms": int(timestamp_ms),
                    "stress": int(stress),
                    "category": category,
                }
            )

        event_timestamp_ms = _record_timestamp(record, value)
        event_date = _record_date(record, value)

        provenance = _provenance(
            "all_day_stress",
            "all_day_stress",
            "confirmed",
        )

        # Preserve common device provenance even if the generic helper does
        # not currently include these keys.
        if not isinstance(provenance, dict):
            provenance = {}

        for key in (
            "deviceId",
            "deviceSn",
            "deviceSource",
            "deviceMac",
        ):
            if key in record and key not in provenance:
                provenance[key] = record.get(key)
            elif key in value and key not in provenance:
                provenance[key] = value.get(key)

        normalized.append(
            {
                "event_type": "all_day_stress",
                "event_timestamp_ms": (
                    int(event_timestamp_ms)
                    if isinstance(event_timestamp_ms, (int, float))
                    else None
                ),
                "date": event_date,
                "min_stress": _int_or_none(value.get("minStress")),
                "max_stress": _int_or_none(value.get("maxStress")),
                "avg_stress": _int_or_none(value.get("avgStress")),
                "relax_proportion": _int_or_none(
                    value.get("relaxProportion")
                ),
                "normal_proportion": _int_or_none(
                    value.get("normalProportion")
                ),
                "medium_proportion": _int_or_none(
                    value.get("mediumProportion")
                ),
                "high_proportion": _int_or_none(
                    value.get("highProportion")
                ),
                "samples": samples,
                "sample_count": len(samples),
                "provenance": provenance,
                "raw": record,
            }
        )

    return normalized


def normalize_wake_data(data: Any) -> list[dict[str, Any]]:
    """Normalize Charge/wake_data samples, which are nested under value.samples."""
    return _normalize_sample_records(
        data,
        "Charge",
        "wake_data",
        WAKE_FIELDS,
        confidence="confirmed",
        sample_date_resolver=_wake_sample_date,
    )


EXERTION_FIELDS = (
    "recoveryFactor",
    "recoveryFactorID",
    "totalScore",
    "activityScore",
    "exerciseScore",
    "targetScore",
    "completionPercent",
    "atl",
    "ctl",
    "tsb",
    "insightState",
)


def normalize_exertion_data(data: Any) -> list[dict[str, Any]]:
    """Normalize Zepp exertion/algo_result without recalculating native values.

    The nested exercisePlan is exposed as convenience fields while the complete
    native payload remains available in raw_value.
    """
    rows = _normalize_value_records(
        data,
        "exertion",
        "algo_result",
        EXERTION_FIELDS,
        confidence="confirmed",
    )
    for row in rows:
        raw_value = row.get("raw_value")
        plan = raw_value.get("exercisePlan") if isinstance(raw_value, dict) else None
        if not isinstance(plan, dict):
            continue
        if "intensity" in plan:
            row["exercise_plan_intensity"] = plan["intensity"]
        if "duration" in plan:
            row["exercise_plan_duration"] = plan["duration"]
        if "heartRateLower" in plan:
            row["exercise_plan_hr_lower"] = plan["heartRateLower"]
        if "heartRateUpper" in plan:
            row["exercise_plan_hr_upper"] = plan["heartRateUpper"]
    return rows


def _wake_timezone_name(value: Any) -> str | None:
    """Parse the exact Zepp wake_data timezone forms observed in production."""
    if not value:
        return None
    timezone_name = str(value).strip()
    if "," in timezone_name:
        prefix, candidate = timezone_name.split(",", 1)
        if prefix.isdigit() and candidate:
            timezone_name = candidate
    try:
        ZoneInfo(timezone_name)
    except (KeyError, ValueError):
        return None
    return timezone_name


def _wake_sample_date(
    record: dict[str, Any],
    payload: dict[str, Any],
    sample: dict[str, Any],
    parent_date: str | None,
    sample_timestamp: Any,
) -> str | None:
    """Resolve Charge/wake_data to the local wake day, not its parent UTC day."""
    explicit_sample_day = _first_value(sample, "date", "day", "dayId")
    if explicit_sample_day is not None:
        return str(explicit_sample_day)

    raw_timezone = _first_value(
        sample, "timezone", "timeZone", "tz",
        default=_first_value(payload, "timezone", "timeZone", "tz"),
    )
    timezone_name = _wake_timezone_name(raw_timezone)
    explicit_sample_timestamp = _as_number(
        _first_value(sample, "timestamp", "time", "startTime")
    )
    wake_timestamp = (
        explicit_sample_timestamp
        if isinstance(explicit_sample_timestamp, (int, float))
        else sample_timestamp
    )
    parent_start = _as_number(
        _first_value(payload, "startTime", "start_time", "start")
    )
    offset = _as_number(_first_value(sample, "s", "start_offset_ms"))
    has_wake_clock = (
        isinstance(explicit_sample_timestamp, (int, float))
        or (
            isinstance(parent_start, (int, float))
            and isinstance(offset, (int, float))
        )
    )
    if has_wake_clock and timezone_name:
        wake_local = _local_datetime(wake_timestamp, timezone_name)
        if wake_local:
            return wake_local.date().isoformat()

    return parent_date


WAKE_DIAGNOSTIC_TIME_FIELDS = (
    "date", "day", "dayId", "timestamp", "time", "startTime", "endTime",
    "timezone", "timeZone", "tz", "utcOffset",
)
WAKE_DIAGNOSTIC_SAMPLE_FIELDS = (
    "s", "timestamp", "time", "date", "day", "dayId", "startTime", "endTime",
    "timezone", "timeZone", "tz", "utcOffset", "bioChargeWake", "wakeCharge",
    "physicalWake", "mentalWake", "dailyFitnessScore", "stressFitnessScore",
    "exertionScore",
)
WAKE_DIAGNOSTIC_STRUCTURAL_FIELDS = {
    "value", "samples", "sample", "items", "data", "records", "result",
}


def _diagnostic_fields(
    mapping: dict[str, Any], allowed: tuple[str, ...]
) -> dict[str, Any]:
    """Return only explicitly allow-listed wake/date fields."""
    return {name: mapping[name] for name in allowed if name in mapping}


def _diagnostic_unknown_keys(
    mapping: dict[str, Any], allowed: tuple[str, ...]
) -> list[str]:
    """Preserve schema clues without exposing unknown values."""
    known = set(allowed) | WAKE_DIAGNOSTIC_STRUCTURAL_FIELDS | {
        "eventType", "subType", "event_type", "sub_type",
    }
    return sorted(str(key) for key in mapping if key not in known)


def _diagnostic_iso(timestamp: Any, timezone_name: str | None) -> str | None:
    parsed = _local_datetime(timestamp, timezone_name)
    return parsed.isoformat() if parsed else None


def diagnose_wake_energy_payload(
    data: Any, *, display_timezone: str = "Europe/Ljubljana"
) -> dict[str, Any]:
    """Describe Charge/wake_data structure and current date resolution safely."""
    records = _event_records(data)
    report_records: list[dict[str, Any]] = []
    normalized_total = 0
    for record_index, record in enumerate(records):
        value = record.get("value")
        value_mapping = value if isinstance(value, dict) else {}
        payload = _record_payload(record)
        raw_timezone = _record_timezone(payload)
        timezone_name = _wake_timezone_name(raw_timezone)
        parent_timestamp = _record_timestamp(record, payload)
        normalized = normalize_wake_data({"items": [record]})
        normalized_total += len(normalized)
        samples = _sample_source(record, payload)
        report_samples: list[dict[str, Any]] = []
        for sample_index, sample in enumerate(samples):
            normalized_row = normalized[sample_index] if sample_index < len(normalized) else None
            raw_sample_timestamp = _first_value(
                sample, "timestamp", "time", "startTime"
            )
            report_samples.append({
                "sample_index": sample_index,
                "fields": _diagnostic_fields(sample, WAKE_DIAGNOSTIC_SAMPLE_FIELDS),
                "unknown_relevant_field_names": _diagnostic_unknown_keys(
                    sample, WAKE_DIAGNOSTIC_SAMPLE_FIELDS
                ),
                "raw_sample_timestamp": raw_sample_timestamp,
                "raw_sample_timestamp_local": _diagnostic_iso(
                    raw_sample_timestamp, timezone_name or display_timezone
                ),
                "resolved_event_date": (
                    normalized_row.get("date") if normalized_row else None
                ),
                "normalized_wake_energy_event_date": (
                    normalized_row.get("date") if normalized_row else None
                ),
                "normalized_sample_timestamp": (
                    normalized_row.get("sample_timestamp") if normalized_row else None
                ),
            })
        # Direct wake fields become one fallback sample when samples are absent or
        # empty. Make that behavior explicit without inventing a raw sample.
        if not samples and normalized:
            row = normalized[0]
            report_samples.append({
                "sample_index": None,
                "source": "parent_fallback",
                "fields": _diagnostic_fields(payload, WAKE_DIAGNOSTIC_SAMPLE_FIELDS),
                "unknown_relevant_field_names": _diagnostic_unknown_keys(
                    payload, WAKE_DIAGNOSTIC_SAMPLE_FIELDS
                ),
                "raw_sample_timestamp": None,
                "raw_sample_timestamp_local": None,
                "resolved_event_date": row.get("date"),
                "normalized_wake_energy_event_date": row.get("date"),
                "normalized_sample_timestamp": row.get("sample_timestamp"),
            })
        report_records.append({
            "record_index": record_index,
            "eventType": _first_value(payload, "eventType", "event_type"),
            "subType": _first_value(payload, "subType", "sub_type"),
            "raw_parent_fields": _diagnostic_fields(
                record, WAKE_DIAGNOSTIC_TIME_FIELDS
            ),
            "raw_value_fields": _diagnostic_fields(
                value_mapping, WAKE_DIAGNOSTIC_TIME_FIELDS
            ),
            "raw_parent_timestamp": parent_timestamp,
            "raw_parent_timestamp_utc": _diagnostic_iso(parent_timestamp, "UTC"),
            "raw_parent_timestamp_local": _diagnostic_iso(
                parent_timestamp, timezone_name or display_timezone
            ),
            "raw_timezone": raw_timezone,
            "effective_timezone": timezone_name,
            "generic_parent_date": _record_date(record, payload),
            "resolved_event_date": (
                normalized[0].get("date")
                if normalized
                else _record_date(record, payload)
            ),
            "value_type": type(value).__name__,
            "samples_present": "samples" in payload or "sample" in payload,
            "sample_count": len(samples),
            "normalized_row_count": len(normalized),
            "unknown_parent_field_names": _diagnostic_unknown_keys(
                record, WAKE_DIAGNOSTIC_TIME_FIELDS
            ),
            "unknown_value_field_names": _diagnostic_unknown_keys(
                value_mapping, WAKE_DIAGNOSTIC_TIME_FIELDS + WAKE_FIELDS
            ),
            "samples": report_samples,
        })
    return {
        "event_contract": {
            "method": "GET",
            "path": "/v2/users/me/events",
            "eventType": "Charge",
            "subType": "wake_data",
        },
        "privacy": (
            "Allow-listed wake/date values only; unknown fields are names only. "
            "Credentials, headers, cookies, user IDs, GPS, and unrelated records are omitted."
        ),
        "display_timezone": display_timezone,
        "raw_record_count": len(records),
        "normalized_row_count": normalized_total,
        "records": report_records,
    }


def _wake_diagnostic_window(
    from_date: str, to_date: str, timezone_name: str
) -> tuple[int, int]:
    start_day = date.fromisoformat(from_date)
    end_day = date.fromisoformat(to_date)
    if end_day < start_day:
        raise ValueError("--to-date must not be before --from-date")
    zone = ZoneInfo(timezone_name)
    start = datetime.combine(start_day, datetime.min.time(), tzinfo=zone)
    end = datetime.combine(
        end_day + timedelta(days=1), datetime.min.time(), tzinfo=zone
    )
    return int(start.timestamp() * 1000), int(end.timestamp() * 1000)


def _diagnostic_wake_db_rows(path: Path, from_date: str, to_date: str) -> list[dict[str, Any]]:
    """Read a minimal wake-only view without migrating or modifying SQLite."""
    if not path.is_file():
        return []
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """SELECT record_key, event_date, timestamp_ms, start_time_ms,
                      sample_timestamp_ms, offset_ms, bio_charge_wake,
                      wake_charge, physical_wake, mental_wake,
                      daily_fitness_score, stress_fitness_score,
                      exertion_score, updated_at
               FROM wake_energy
               WHERE event_date BETWEEN ? AND ?
               ORDER BY event_date, COALESCE(sample_timestamp_ms, timestamp_ms),
                        record_key""",
            (from_date, to_date),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()


def cmd_diagnose_wake_energy(args: argparse.Namespace) -> None:
    cfg = load_config()
    display_timezone = (
        args.timezone or cfg.get("timezone") or FRESHNESS_TIMEZONE
    )
    try:
        from_ms, to_ms = _wake_diagnostic_window(
            args.from_date, args.to_date, display_timezone
        )
    except (ValueError, KeyError) as exc:
        sys.exit(f"Invalid diagnostic date range/timezone: {exc}")
    client = _load_client()
    payload = client.events(
        "Charge", "wake_data", from_ms, to_ms, limit=args.limit
    )
    report = diagnose_wake_energy_payload(
        payload, display_timezone=display_timezone
    )
    report["request"] = {
        "from_date": args.from_date,
        "to_date": args.to_date,
        "from_ms": from_ms,
        "to_ms": to_ms,
        "timezone": display_timezone,
        "limit": args.limit,
        "reverse": 1,
    }
    db_path = resolve_db_path(getattr(args, "db_path", None), cfg)
    report["sqlite"] = {
        "database_path": str(db_path),
        "database_exists": db_path.is_file(),
        "wake_energy_rows": _diagnostic_wake_db_rows(
            db_path, args.from_date, args.to_date
        ),
    }
    _emit_json(report, args)


def normalize_readiness_data(data: Any) -> list[dict[str, Any]]:
    """Normalize the native readiness/watch_score value without interpreting status."""
    rows = _normalize_value_records(
        data, "readiness", "watch_score", READINESS_FIELDS, confidence="confirmed"
    )
    sentinel_fields = {
        "phyScore", "phyInsight", "phyBaseline", "mentScore", "mentInsight",
        "mentBaseLine", "hrvInsight", "rhrInsight", "skinTempInsight",
        "ahiInsight", "afibScore", "afibInsight",
    }
    for row in rows:
        row["status_mapping_confidence"] = "unknown"
        if any(row.get(field) == 255 for field in sentinel_fields):
            row["sentinel_255_semantics"] = "unknown"
    return rows


def latest_readiness_per_day(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Select one readiness record per date using Zepp update timestamps.

    The effective ordering is timestampUpdate, falling back to timestamp when
    timestampUpdate is absent. Exact ties retain the first input record.
    Records without a date are preserved because they cannot be assigned to a
    daily bucket safely.
    """
    selected: dict[str, tuple[int, dict[str, Any]]] = {}
    order: list[str] = []

    def number(row: dict[str, Any], key: str) -> int | float | None:
        value = _as_number(row.get(key))
        return value if isinstance(value, (int, float)) else None

    def rank(row: dict[str, Any]) -> tuple[int, int | float, int | float]:
        updated = number(row, "timestampUpdate")
        timestamp = number(row, "timestamp")
        effective = updated if updated is not None else timestamp
        return (
            1 if effective is not None else 0,
            effective if effective is not None else -1,
            timestamp if timestamp is not None else -1,
        )

    for index, row in enumerate(rows):
        day = row.get("date")
        if not day:
            order.append(f"__missing_date_{index}")
            selected[order[-1]] = (index, row)
            continue
        key = str(day)
        if key not in selected:
            order.append(key)
            selected[key] = (index, row)
            continue
        current = selected[key][1]
        if rank(row) > rank(current):
            selected[key] = (index, row)
    return [selected[key][1] for key in order]


def _print_rows(title: str, rows: list[dict[str, Any]], columns: tuple[str, ...]) -> None:
    print(f"{title}\n")
    if not rows:
        print("No records.")
        return
    print("  " + "  ".join(f"{column:<20}" for column in columns))
    for row in rows:
        values = []
        for column in columns:
            value = row.get(column)
            if column in ("s", "e", "offset", "start_offset_ms", "end_offset_ms"):
                value = _format_offset(value)
            values.append(str(value if value is not None else "—"))
        print("  " + "  ".join(f"{value:<20}" for value in values))


def cmd_hrv(args: argparse.Namespace) -> None:
    c = _load_client()
    from_ms, to_ms = _ms_window(args.days)
    rows = normalize_hrv_data(c.events("HRVRMSSD", "real_data", from_ms, to_ms, limit=args.limit))
    if args.json:
        _emit_json(rows, args)
    else:
        _print_rows("Zepp HRV / RMSSD-like samples", rows, ("date", "time", "hrv", "offset", "raw_u"))


def cmd_wake_energy(args: argparse.Namespace) -> None:
    c = _load_client()
    from_ms, to_ms = _ms_window(args.days)
    rows = normalize_wake_data(c.events("Charge", "wake_data", from_ms, to_ms, limit=args.limit))
    if args.json:
        _emit_json(rows, args)
    else:
        _print_rows("Zepp Charge wake_data samples", rows, ("date", "bioChargeWake", "wakeCharge", "physicalWake", "mentalWake", "dailyFitnessScore", "stressFitnessScore", "exertionScore"))


def cmd_exertion(args: argparse.Namespace) -> None:
    c = _load_client()
    from_ms, to_ms = _ms_window(args.days)
    rows = normalize_exertion_data(
        c.events("exertion", "algo_result", from_ms, to_ms, limit=args.limit)
    )
    if args.json:
        _emit_json(rows, args)
    else:
        _print_rows(
            "Zepp exertion / algorithm results",
            rows,
            (
                "date",
                "recoveryFactor",
                "recoveryFactorID",
                "totalScore",
                "activityScore",
                "exerciseScore",
                "targetScore",
                "completionPercent",
                "atl",
                "ctl",
                "tsb",
                "insightState",
                "exercise_plan_intensity",
                "exercise_plan_duration",
                "exercise_plan_hr_lower",
                "exercise_plan_hr_upper",
            ),
        )


def _decode_json_mapping(value: Any) -> dict[str, Any]:
    """Decode an observed Zepp JSON object while preserving unknown structures."""
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return {}
    try:
        decoded = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _find_native_field(value: Any, field: str) -> Any:
    """Return the first exact native field name found in a PHN state tree."""
    if isinstance(value, dict):
        if field in value:
            return value[field]
        for nested in value.values():
            found = _find_native_field(nested, field)
            if found is not None:
                return found
    elif isinstance(value, list):
        for nested in value:
            found = _find_native_field(nested, field)
            if found is not None:
                return found
    return None


PHN_RECORD_FIELDS = (
    "flag",
    "degree_of_completion",
    "degree_of_completion_week",
    "phn_plan_id",
)


PHN_TRAINING_PLAN_FIELDS = (
    "phn_plan_id",
    "last_update_time",
    "exercise_day",
    "training_days",
    "weekly_high_intensity_day",
    "current_weekday",
    "flag_recommended_exercise",
    "trimp_daily_recommended",
    "daily_recommend_intensity",
    "duration_zone1",
    "duration_zone2",
    "duration_zone3",
    "yesterday_recommend_flag",
    "this_week_achieved_daily_completed_percent",
)


def normalize_phn_record_data(data: Any) -> list[dict[str, Any]]:
    """Normalize historical PHN/record events without interpreting flag meaning.

    Observed Zepp payloads may store `value.record` as an escaped JSON string.
    The original complete event payload remains available in `raw_value`.
    """
    normalized: list[dict[str, Any]] = []
    provenance = _provenance(
        "phn", "record", "confirmed"
    )

    for event in _event_records(data):
        payload = _record_payload(event)

        native_record = _decode_json_mapping(
            payload.get("record")
        )

        # Some Zepp variants may expose record fields directly.
        if not native_record:
            native_record = {
                field: payload[field]
                for field in PHN_RECORD_FIELDS
                if field in payload
            }

        item: dict[str, Any] = {
            "date": _record_date(event, payload),
            "timestamp": _record_timestamp(event, payload),
            **provenance,
        }

        for field in PHN_RECORD_FIELDS:
            value = native_record.get(field)
            if value is None and field in payload:
                value = payload.get(field)
            if value is not None:
                item[field] = value

        item["raw_value"] = payload
        item["decoded_record"] = native_record
        normalized.append(item)

    return normalized


def normalize_phn_training_plan_data(
    data: Any,
) -> list[dict[str, Any]]:
    """Normalize PHN/training_plan snapshots.

    `training_plan` is treated as mutable native Coach state. No PHN flag
    thresholds or training recommendations are recalculated here.
    """
    normalized: list[dict[str, Any]] = []
    provenance = _provenance(
        "phn", "training_plan", "confirmed"
    )

    for event in _event_records(data):
        payload = _record_payload(event)

        result = _decode_json_mapping(
            payload.get("result")
        )

        # Captures have shown both nested result JSON and direct mappings.
        state: Any = result if result else payload

        item: dict[str, Any] = {
            "date": _record_date(event, payload),
            "timestamp": _record_timestamp(event, payload),
            **provenance,
        }

        for field in PHN_TRAINING_PLAN_FIELDS:
            value = _find_native_field(state, field)
            if value is None:
                value = _find_native_field(payload, field)
            if value is not None:
                item[field] = value

        # Production-verified identity contract:
        # phn/record.phn_plan_id == phn/training_plan event timestamp.
        #
        # The mutable result object does not currently expose phn_plan_id,
        # therefore preserve the native event timestamp as its factual
        # plan identity fallback.
        if item.get("phn_plan_id") is None:
            event_timestamp = item.get("timestamp")
            if event_timestamp is not None:
                item["phn_plan_id"] = event_timestamp

        item["raw_value"] = payload
        item["decoded_result"] = result
        normalized.append(item)

    return normalized


def fetch_current_phn_training_plan(
    client: ZeppClient,
    days: int = 30,
    *,
    record_limit: int = 2000,
    plan_limit: int = 50,
) -> dict[str, Any]:
    """Resolve the current mutable PHN training plan through latest phn_plan_id.

    Production evidence:
    - phn/record is ordinary recent history
    - record.phn_plan_id matches training_plan event.timestamp
    - training_plan event timestamp can be old/stable
    - last_update_time inside the plan represents current mutable-state freshness
    """
    record_from_ms, record_to_ms = _ms_window(days)

    record_payload = client.events(
        "phn",
        "record",
        record_from_ms,
        record_to_ms,
        limit=record_limit,
        reverse=True,
    )
    record_rows = normalize_phn_record_data(record_payload)

    candidates = [
        row for row in record_rows
        if row.get("phn_plan_id") is not None
    ]

    if not candidates:
        return {
            "status": "no_plan_identity",
            "phn_plan_id": None,
            "payload": {"items": []},
            "from_ms": record_from_ms,
            "to_ms": record_to_ms,
            "record_items": len(record_rows),
        }

    latest = max(
        candidates,
        key=lambda row: (
            row.get("timestamp") or 0,
            str(row.get("phn_plan_id")),
        ),
    )

    try:
        plan_id = int(latest["phn_plan_id"])
    except (TypeError, ValueError):
        return {
            "status": "invalid_plan_identity",
            "phn_plan_id": latest.get("phn_plan_id"),
            "payload": {"items": []},
            "from_ms": record_from_ms,
            "to_ms": record_to_ms,
            "record_items": len(record_rows),
        }

    margin_ms = 5 * 60 * 1000

    plan_from_ms = plan_id - margin_ms
    plan_to_ms = plan_id + margin_ms

    payload = client.events(
        "phn",
        "training_plan",
        plan_from_ms,
        plan_to_ms,
        limit=plan_limit,
        reverse=True,
    )

    items = payload.get("items") if isinstance(payload, dict) else None
    items = items if isinstance(items, list) else []

    exact = [
        item for item in items
        if isinstance(item, dict)
        and item.get("timestamp") == plan_id
    ]

    selected = exact[:1]

    if not selected:
        candidates_with_ts = [
            item for item in items
            if isinstance(item, dict)
            and isinstance(item.get("timestamp"), (int, float))
        ]
        if candidates_with_ts:
            selected = [
                min(
                    candidates_with_ts,
                    key=lambda item: abs(
                        int(item["timestamp"]) - plan_id
                    ),
                )
            ]

    return {
        "status": "ok" if selected else "not_found",
        "phn_plan_id": plan_id,
        "payload": {"items": selected},
        "raw_candidate_count": len(items),
        "from_ms": plan_from_ms,
        "to_ms": plan_to_ms,
        "record_items": len(record_rows),
    }


def cmd_phn_record(args: argparse.Namespace) -> None:
    """Read-only probe of PHN daily record history."""
    client = _load_client()
    from_ms, to_ms = _ms_window(args.days)
    payload = client.events(
        "phn",
        "record",
        from_ms,
        to_ms,
        limit=args.limit,
    )
    rows = normalize_phn_record_data(payload)

    if args.json:
        _emit_json(rows, args)
        return

    _print_rows(
        "Zepp PHN daily records",
        rows,
        (
            "date",
            "flag",
            "degree_of_completion",
            "degree_of_completion_week",
            "phn_plan_id",
        ),
    )


def cmd_phn_training_plan(args: argparse.Namespace) -> None:
    """Read-only probe of the current PHN/Zepp Coach training-plan state."""
    client = _load_client()

    resolved = fetch_current_phn_training_plan(
        client,
        args.days,
        record_limit=max(args.limit, 2000),
        plan_limit=min(max(args.limit, 1), 200),
    )

    rows = normalize_phn_training_plan_data(
        resolved["payload"]
    )

    if args.json:
        _emit_json(rows, args)
        return

    if not rows:
        print(
            "No PHN training plan resolved "
            f"(status={resolved['status']}, "
            f"plan_id={resolved['phn_plan_id']})."
        )
        return

    _print_rows(
        "Zepp PHN training-plan snapshots",
        rows,
        (
            "date",
            "phn_plan_id",
            "exercise_day",
            "current_weekday",
            "flag_recommended_exercise",
            "trimp_daily_recommended",
            "daily_recommend_intensity",
            "yesterday_recommend_flag",
        ),
    )


def sync_phn_domains(
    client: ZeppClient,
    database: Database,
    days: int,
    *,
    limit: int = 2000,
) -> dict[str, Any]:
    """Opt-in PHN synchronization.

    PHN is intentionally not part of the default sync-domain set until the
    current Zepp backend GET behavior is live-validated.
    """
    from_ms, to_ms = _ms_window(days)

    specs = (
        (
            "phn_record",
            "phn",
            "record",
            normalize_phn_record_data,
        ),
        (
            "phn_training_plan",
            "phn",
            "training_plan",
            normalize_phn_training_plan_data,
        ),
    )

    results: list[dict[str, Any]] = []

    for domain, event_type, sub_type, normalizer in specs:
        try:
            domain_from_ms = from_ms
            domain_to_ms = to_ms

            if domain == "phn_training_plan":
                resolved = fetch_current_phn_training_plan(
                    client,
                    days,
                    record_limit=limit,
                    plan_limit=50,
                )
                payload = resolved["payload"]
                domain_from_ms = resolved["from_ms"]
                domain_to_ms = resolved["to_ms"]
            else:
                payload = client.events(
                    event_type,
                    sub_type,
                    from_ms,
                    to_ms,
                    limit=limit,
                )

            rows = normalizer(payload)

            counts = database.store_domain_with_raw(
                domain,
                event_type,
                sub_type,
                payload,
                domain_from_ms,
                domain_to_ms,
                rows,
            )

            results.append({
                "domain": domain,
                "event_type": event_type,
                "sub_type": sub_type,
                "status": "ok" if rows else "empty",
                "records_retrieved": len(rows),
                **counts,
            })
        except Exception as exc:
            results.append({
                "domain": domain,
                "event_type": event_type,
                "sub_type": sub_type,
                "status": "error",
                "records_retrieved": 0,
                "inserted": 0,
                "updated": 0,
                "unchanged": 0,
                "error": type(exc).__name__,
            })

    statuses = {row["status"] for row in results}
    overall = (
        "ok"
        if statuses <= {"ok", "empty"}
        else "partial"
        if "ok" in statuses or "empty" in statuses
        else "error"
    )

    return {
        "status": overall,
        "requested_days": days,
        "domains": results,
    }


def cmd_sync_phn(args: argparse.Namespace) -> None:
    database = Database(_db_path_from_args(args))
    try:
        result = sync_phn_domains(
            _load_client(),
            database,
            args.days,
            limit=args.limit,
        )
    finally:
        database.close()

    if args.json:
        _emit_json(result, args)
        return

    print(
        f"PHN synchronization: "
        f"{result['status']} ({result['requested_days']} days)"
    )
    for domain in result["domains"]:
        print(
            f"  {domain['domain']}: {domain['status']} "
            f"retrieved={domain['records_retrieved']} "
            f"inserted={domain['inserted']} "
            f"updated={domain['updated']} "
            f"unchanged={domain['unchanged']}"
            + (
                f" error={domain['error']}"
                if domain.get("error")
                else ""
            )
        )


def cmd_lifeload(args: argparse.Namespace) -> None:
    c = _load_client()
    from_ms, to_ms = _ms_window(args.days)
    rows = _normalize_value_records(
        c.events("LifeLoad", "summary", from_ms, to_ms, limit=args.limit),
        "LifeLoad", "summary", ("lifeLoad",), confidence="candidate",
    )
    if args.json:
        _emit_json(rows, args)
    else:
        _print_rows("Zepp LifeLoad", rows, ("date", "lifeLoad"))
        print("\nLifeLoad is a candidate related to Zepp's daily BioCharge/body-energy model; exact UI equivalence is not confirmed.")


def cmd_readiness(args: argparse.Namespace) -> None:
    c = _load_client()
    from_ms, to_ms = _ms_window(args.days)
    rows = normalize_readiness_data(c.events("readiness", "watch_score", from_ms, to_ms, limit=args.limit))
    if getattr(args, "latest_per_day", False):
        rows = latest_readiness_per_day(rows)
    if args.json:
        _emit_json(rows, args)
    else:
        title = "Zepp readiness/watch_score values"
        if getattr(args, "latest_per_day", False):
            title += " (latest per day)"
        _print_rows(title, rows, ("date", "status", "hrvScore", "sleepHRV", "rhrScore", "sleepRHR", "phyScore", "mentScore", "skinTempScore", "ahiScore", "rdnsScore"))


def cmd_sleep_status(args: argparse.Namespace) -> None:
    c = _load_client()
    from_ms, to_ms = _ms_window(args.days)
    rows = normalize_readiness_data(c.events("readiness", "watch_score", from_ms, to_ms, limit=args.limit))
    if args.json:
        _emit_json(rows, args)
    else:
        _print_rows("Zepp sleep-related readiness/watch_score values", rows, ("date", "sleepHRV", "sleepRHR", "ahiScore", "ahiBaseline", "rdnsScore"))


def cmd_charge_data(args: argparse.Namespace) -> None:
    c = _load_client()
    from_ms, to_ms = _ms_window(args.days)
    rows = normalize_charge_data(c.events("Charge", "real_data", from_ms, to_ms, limit=args.limit))
    if args.json:
        _emit_json(rows, args)
    else:
        _print_rows("Zepp Charge real_data samples", rows, ("date", "s", "e", "total", "physical", "mental"))


def consolidate_daily_status(
    hrv_rows: list[dict[str, Any]],
    wake_rows: list[dict[str, Any]],
    exertion_rows: list[dict[str, Any]],
    lifeload_rows: list[dict[str, Any]],
    readiness_rows: list[dict[str, Any]] | None = None,
    sleep_rows: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    readiness_rows = latest_readiness_per_day(readiness_rows or [])
    sleep_rows = latest_readiness_per_day(sleep_rows or [])

    def bucket(day: str | None) -> dict[str, Any] | None:
        if not day:
            return None
        return grouped.setdefault(day, {"date": day})

    for row in hrv_rows:
        target = bucket(row.get("date"))
        if target is None:
            continue
        current = target.get("hrv")
        if current is None or (row.get("sample_timestamp") or 0) >= (current.get("sample_timestamp") or 0):
            target["hrv"] = {
                "latest": row.get("hrv"),
                "sample_timestamp": row.get("sample_timestamp"),
                "source": "zepp",
                "calculation_source": "zepp",
                "mapping_confidence": "confirmed",
            }
        target.setdefault("hrv_sample_count", 0)
        target["hrv_sample_count"] += 1
    for name, rows, fields in (
        ("wake_energy", wake_rows, ("bioChargeWake", "wakeCharge", "physicalWake", "mentalWake", "dailyFitnessScore", "stressFitnessScore", "exertionScore")),
        (
            "exertion",
            exertion_rows,
            (
                "recoveryFactor",
                "recoveryFactorID",
                "totalScore",
                "activityScore",
                "exerciseScore",
                "targetScore",
                "completionPercent",
                "atl",
                "ctl",
                "tsb",
                "insightState",
                "exercise_plan_intensity",
                "exercise_plan_duration",
                "exercise_plan_hr_lower",
                "exercise_plan_hr_upper",
            ),
        ),
        ("lifeload", lifeload_rows, ("lifeLoad",)),
        ("readiness", readiness_rows, READINESS_FIELDS),
        ("sleep_related_readiness", sleep_rows, ("sleepHRV", "sleepRHR", "ahiScore", "ahiBaseline", "rdnsScore")),
    ):
        for row in rows:
            target = bucket(row.get("date"))
            if target is None:
                continue
            target[name] = {
                field: row.get(field) for field in fields if field in row
            }
            target[name].update({
                "source": row["source"],
                "calculation_source": row["calculation_source"],
                "mapping_confidence": row["mapping_confidence"],
            })
            for metadata in ("status_mapping_confidence", "sentinel_255_semantics"):
                if metadata in row:
                    target[name][metadata] = row[metadata]
    return [grouped[key] for key in sorted(grouped)]


def cmd_daily_status(args: argparse.Namespace) -> None:
    if getattr(args, "from_db", False):
        end = _today_utc()
        start = end - timedelta(days=args.days - 1)
        database = Database(_db_path_from_args(args))
        try:
            rows = database.read_daily_status(start.isoformat(), end.isoformat())
        finally:
            database.close()
        if args.json:
            _emit_json(rows, args)
            return
        print(f"Daily Zepp status from SQLite ({args.days} days)\n")
        for row in rows:
            print(row["date"])
            if "hrv" in row:
                print(f"  HRV latest={row['hrv']['latest']} samples={row['hrv_sample_count']}")
            for section in ("wake_energy", "exertion", "lifeload", "readiness", "sleep_related_readiness"):
                if section in row:
                    values = " ".join(f"{k}={v}" for k, v in row[section].items() if k not in ("source", "calculation_source", "mapping_confidence"))
                    print(f"  {section}: {values}")
        return
    c = _load_client()
    from_ms, to_ms = _ms_window(args.days)
    hrv = normalize_hrv_data(c.events("HRVRMSSD", "real_data", from_ms, to_ms, limit=args.limit))
    wake = normalize_wake_data(c.events("Charge", "wake_data", from_ms, to_ms, limit=args.limit))
    exertion = normalize_exertion_data(
        c.events("exertion", "algo_result", from_ms, to_ms, limit=args.limit)
    )
    lifeload = _normalize_value_records(c.events("LifeLoad", "summary", from_ms, to_ms, limit=args.limit), "LifeLoad", "summary", ("lifeLoad",), confidence="candidate")
    readiness = normalize_readiness_data(c.events("readiness", "watch_score", from_ms, to_ms, limit=args.limit))
    rows = consolidate_daily_status(hrv, wake, exertion, lifeload, readiness, readiness)
    if args.json:
        _emit_json(rows, args)
    else:
        print(f"Daily Zepp status ({args.days} days)\n")
        for row in rows:
            print(f"{row['date']}")
            if "hrv" in row:
                print(f"  HRV latest={row['hrv']['latest']} samples={row['hrv_sample_count']}")
            for section in ("wake_energy", "exertion", "lifeload", "readiness", "sleep_related_readiness"):
                if section in row:
                    values = " ".join(f"{k}={v}" for k, v in row[section].items() if k not in ("source", "calculation_source", "mapping_confidence"))
                    print(f"  {section}: {values}")


SYNC_DOMAIN_SPECS: tuple[tuple[str, str, str, Any], ...] = (
    ("hrv", "HRVRMSSD", "real_data", normalize_hrv_data),
    ("wake_energy", "Charge", "wake_data", normalize_wake_data),
    ("exertion", "exertion", "algo_result", normalize_exertion_data),
    ("readiness", "readiness", "watch_score", normalize_readiness_data),
    ("charge", "Charge", "real_data", normalize_charge_data),
    ("insights", "Charge", "insight_data", lambda data: _insight_rows(normalize_insight_data(data))),
    ("lifeload", "LifeLoad", "summary", lambda data: _normalize_value_records(data, "LifeLoad", "summary", ("lifeLoad",), confidence="candidate")),
)


def fetch_sport_load_pages(
    client: ZeppClient,
    start_day: date,
    end_day: date,
    *,
    limit: int = 900,
    max_pages: int = 100,
) -> tuple[list[dict[str, Any]], int]:
    """Fetch cursor-paginated SPORT_LOAD rows and deduplicate by event date."""
    cursor: int | None = None
    seen_cursors: set[int] = set()
    by_date: dict[str, dict[str, Any]] = {}
    pages = 0
    while pages < max_pages:
        payload = client.sport_load(
            start_day,
            end_day,
            limit=limit,
            next_cursor=cursor,
        )
        pages += 1
        for row in normalize_sport_load_data(payload):
            by_date.setdefault(row["event_date"], row)
        next_value = payload.get("next") if isinstance(payload, dict) else None
        next_cursor = _int_or_none(next_value)
        if next_cursor is None:
            break
        if next_cursor in seen_cursors:
            raise RuntimeError("SPORT_LOAD pagination cursor repeated")
        seen_cursors.add(next_cursor)
        cursor = next_cursor
    else:
        raise RuntimeError("SPORT_LOAD pagination exceeded page limit")
    return sorted(by_date.values(), key=lambda row: row["event_date"]), pages


def _sync_summary_result(
    domain: str,
    event_type: str,
    sub_type: str,
    *,
    status: str,
    records_retrieved: int = 0,
    inserted: int = 0,
    updated: int = 0,
    unchanged: int = 0,
    error: str | None = None,
) -> dict[str, Any]:
    return {
        "domain": domain,
        "event_type": event_type,
        "sub_type": sub_type,
        "status": status,
        "records_retrieved": records_retrieved,
        "inserted": inserted,
        "updated": updated,
        "unchanged": unchanged,
        "error": error,
    }


def sync_native_metrics(
    client: ZeppClient,
    database: Database,
    days: int,
    *,
    limit: int = 2000,
) -> dict[str, Any]:
    """Fetch and persist native domains independently, continuing after errors."""
    from_ms, to_ms = _ms_window(days)
    database.mark_running_syncs_interrupted()
    run_id = database.start_sync(days, from_ms, to_ms)
    started = time.perf_counter()
    domains: list[dict[str, Any]] = []
    try:
        for domain, event_type, sub_type, normalizer in SYNC_DOMAIN_SPECS:
            try:
                payload = client.events(event_type, sub_type, from_ms, to_ms, limit=limit)
                rows = normalizer(payload)
                counts = database.store_domain_with_raw(
                    domain, event_type, sub_type, payload, from_ms, to_ms, rows
                )
                result = _sync_summary_result(
                    domain, event_type, sub_type,
                    status="ok" if rows else "empty",
                    records_retrieved=len(rows),
                    **counts,
                )
            except Exception as exc:
                # Do not expose request URLs, response text, or config values.
                result = _sync_summary_result(
                    domain, event_type, sub_type,
                    status="error",
                    error=type(exc).__name__,
                )
            domains.append(result)
            database.record_sync_domain(run_id, result)
        # all_day_stress is persisted separately because one native event
        # contains both an authoritative daily summary and a sparse
        # five-minute timeline.
        domain = "stress"
        event_type = "all_day_stress"
        sub_type = "all_day_stress"

        try:
            payload = client.events(
                event_type,
                sub_type,
                from_ms,
                to_ms,
                limit=limit,
                reverse=True,
            )

            rows = normalize_stress_data(payload)
            counts = database.store_stress_rows(rows)

            result = _sync_summary_result(
                domain,
                event_type,
                sub_type,
                status="ok" if rows else "empty",
                records_retrieved=len(rows),
                inserted=(
                    counts["daily_inserted"]
                    + counts["samples_inserted"]
                ),
                updated=(
                    counts["daily_updated"]
                    + counts["samples_updated"]
                ),
                unchanged=(
                    counts["daily_unchanged"]
                    + counts["samples_unchanged"]
                ),
            )

            # Keep the detailed Stress persistence counts available in the
            # sync summary without changing the generic sync-domain contract.
            result.update(counts)

        except Exception as exc:
            result = _sync_summary_result(
                domain,
                event_type,
                sub_type,
                status="error",
                error=type(exc).__name__,
            )

        domains.append(result)
        database.record_sync_domain(run_id, result)

        domain = "food"
        event_type = "Food"
        sub_type = "real_data"
        try:
            payload = client.events(
                event_type,
                sub_type,
                from_ms,
                to_ms,
                limit=limit,
                reverse=True,
            )
            rows = normalize_food_data(payload)
            counts = database.store_food_rows(rows)
            result = _sync_summary_result(
                domain,
                event_type,
                sub_type,
                status="ok" if rows else "empty",
                records_retrieved=len(rows),
                **counts,
            )
        except Exception as exc:
            result = _sync_summary_result(
                domain,
                event_type,
                sub_type,
                status="error",
                error=type(exc).__name__,
            )
        domains.append(result)
        database.record_sync_domain(run_id, result)

        domain = "sport_load"
        event_type = "WatchSportStatistics"
        sub_type = "SPORT_LOAD"
        local_today = datetime.now(
            timezone.utc
        ).astimezone(ZoneInfo(FRESHNESS_TIMEZONE)).date()
        start_day = local_today - timedelta(days=days - 1)
        try:
            rows, pages = fetch_sport_load_pages(
                client,
                start_day,
                local_today,
                limit=min(limit, 900),
            )
            counts = database.store_sport_load_rows(rows)
            result = _sync_summary_result(
                domain,
                event_type,
                sub_type,
                status="ok" if rows else "empty",
                records_retrieved=len(rows),
                **counts,
            )
            result["pages"] = pages
        except Exception as exc:
            result = _sync_summary_result(
                domain,
                event_type,
                sub_type,
                status="error",
                error=type(exc).__name__,
            )
        domains.append(result)
        database.record_sync_domain(run_id, result)

        error_count = sum(row["status"] == "error" for row in domains)
        status = "error" if error_count == len(domains) else "partial" if error_count else "ok"
        summary = {
            "requested_days": days,
            "from_ms": from_ms,
            "to_ms": to_ms,
            "database_path": str(database.path),
            "domains": domains,
            "duration_seconds": round(time.perf_counter() - started, 3),
        }
        database.finish_sync(run_id, status, summary)
        return {"sync_run_id": run_id, "status": status, **summary}
    except Exception:
        database.finish_sync(run_id, "error", {"error": "sync_orchestration_error"})
        raise


def _utc_date_window(start_day: date, end_day_exclusive: date) -> tuple[int, int]:
    """Convert an inclusive calendar range to a half-open UTC millisecond range."""
    start = datetime.combine(start_day, datetime.min.time(), tzinfo=timezone.utc)
    end = datetime.combine(end_day_exclusive, datetime.min.time(), tzinfo=timezone.utc)
    return int(start.timestamp() * 1000), int(end.timestamp() * 1000)


def _historical_domain_result(domain: str, event_type: str, sub_type: str, **values: Any) -> dict[str, Any]:
    return {
        "domain": domain, "event_type": event_type, "sub_type": sub_type,
        "status": values.pop("status", "ok"), "target_from_date": values.pop("target_from_date"),
        "cursor_to_date": values.pop("cursor_to_date"), **values,
    }


def backfill_native_metrics(
    client: ZeppClient,
    database: Database,
    days: int,
    *,
    limit: int = 2000,
    chunk_days: int = 30,
) -> dict[str, Any]:
    """Fetch native domains in resumable, backwards calendar chunks.

    The events API has no documented cursor in the response, so date ranges are
    the safe cursor. Each chunk is committed independently and logical keys make
    overlapping/repeated requests idempotent.
    """
    if days < 1 or chunk_days < 1:
        raise ValueError("days and chunk_days must be positive")
    today = _today_utc()
    target_from = today - timedelta(days=days - 1)
    target_from_text = target_from.isoformat()
    job_key = f"backfill:{target_from_text}"
    run_from, run_to = _utc_date_window(target_from, today + timedelta(days=1))
    database.mark_running_syncs_interrupted()
    run_id = database.start_sync(days, run_from, run_to)
    started = time.perf_counter()
    domains: list[dict[str, Any]] = []
    try:
        for domain, event_type, sub_type, normalizer in SYNC_DOMAIN_SPECS:
            progress = database.get_historical_progress(job_key, domain)
            cursor_to = date.fromisoformat(progress["cursor_to_date"]) if progress else today + timedelta(days=1)
            totals = {
                "records_retrieved": int(progress["records_retrieved"]) if progress else 0,
                "inserted": int(progress["inserted_count"]) if progress else 0,
                "updated": int(progress["updated_count"]) if progress else 0,
                "unchanged": int(progress["unchanged_count"]) if progress else 0,
                "chunks_completed": int(progress["chunks_completed"]) if progress else 0,
            }
            if progress and progress["status"] == "complete":
                domains.append(_historical_domain_result(domain, event_type, sub_type, status="complete",
                    target_from_date=target_from_text, cursor_to_date=cursor_to.isoformat(), **totals))
                continue
            status = "complete"
            error = None
            while cursor_to > target_from:
                chunk_from = max(target_from, cursor_to - timedelta(days=chunk_days))
                from_ms, to_ms = _utc_date_window(chunk_from, cursor_to)
                try:
                    payload = client.events(event_type, sub_type, from_ms, to_ms, limit=limit, reverse=True)
                    rows = normalizer(payload)
                    counts = database.store_domain_with_raw(domain, event_type, sub_type, payload, from_ms, to_ms, rows)
                    cursor_to = chunk_from
                    totals["records_retrieved"] += len(rows)
                    totals["chunks_completed"] += 1
                    for key in ("inserted", "updated", "unchanged"):
                        totals[key] += counts[key]
                    database.save_historical_progress(job_key, _historical_domain_result(
                        domain, event_type, sub_type, status="running", target_from_date=target_from_text,
                        cursor_to_date=cursor_to.isoformat(), **totals))
                except Exception as exc:
                    status = "error"
                    error = type(exc).__name__
                    database.save_historical_progress(job_key, _historical_domain_result(
                        domain, event_type, sub_type, status=status, target_from_date=target_from_text,
                        cursor_to_date=cursor_to.isoformat(), error=error, **totals))
                    break
            if status != "error":
                database.save_historical_progress(job_key, _historical_domain_result(
                    domain, event_type, sub_type, status="complete", target_from_date=target_from_text,
                    cursor_to_date=target_from_text, **totals))
            domains.append(_historical_domain_result(domain, event_type, sub_type, status=status,
                target_from_date=target_from_text, cursor_to_date=cursor_to.isoformat(), error=error, **totals))
            database.record_sync_domain(run_id, {
                **domains[-1], "status": status, "inserted": totals["inserted"],
                "updated": totals["updated"], "unchanged": totals["unchanged"],
            })
        domain = "sport_load"
        event_type = "WatchSportStatistics"
        sub_type = "SPORT_LOAD"
        try:
            rows, pages = fetch_sport_load_pages(
                client,
                target_from,
                today,
                limit=min(limit, 900),
            )
            counts = database.store_sport_load_rows(rows)
            sport_result = _historical_domain_result(
                domain,
                event_type,
                sub_type,
                status="complete",
                target_from_date=target_from_text,
                cursor_to_date=target_from_text,
                records_retrieved=len(rows),
                pages=pages,
                chunks_completed=pages,
                **counts,
            )
        except Exception as exc:
            sport_result = _historical_domain_result(
                domain,
                event_type,
                sub_type,
                status="error",
                target_from_date=target_from_text,
                cursor_to_date=today.isoformat(),
                records_retrieved=0,
                inserted=0,
                updated=0,
                unchanged=0,
                pages=0,
                chunks_completed=0,
                error=type(exc).__name__,
            )
        domains.append(sport_result)
        database.record_sync_domain(run_id, sport_result)
        status = "ok" if not any(row["status"] == "error" for row in domains) else "partial"
        summary = {"requested_days": days, "from_ms": run_from, "to_ms": run_to,
                   "database_path": str(database.path), "job_key": job_key, "chunk_days": chunk_days,
                   "domains": domains, "duration_seconds": round(time.perf_counter() - started, 3)}
        database.finish_sync(run_id, status, summary)
        return {"sync_run_id": run_id, "status": status, **summary}
    except Exception:
        database.finish_sync(run_id, "error", {"error": "backfill_orchestration_error", "job_key": job_key})
        raise


def probe_historical_domains(client: ZeppClient, days_list: list[int], *, limit: int = 2000) -> list[dict[str, Any]]:
    """Measure response coverage at several ranges without claiming a server limit."""
    today = _today_utc()
    output: list[dict[str, Any]] = []
    for domain, event_type, sub_type, normalizer in SYNC_DOMAIN_SPECS:
        for days in days_list:
            start = today - timedelta(days=days - 1)
            from_ms, to_ms = _utc_date_window(start, today + timedelta(days=1))
            item: dict[str, Any] = {"domain": domain, "event_type": event_type, "sub_type": sub_type,
                                    "requested_days": days, "from_date": start.isoformat(), "to_date": today.isoformat()}
            try:
                payload = client.events(event_type, sub_type, from_ms, to_ms, limit=limit, reverse=True)
                rows = normalizer(payload)
                dates = sorted({str(row["date"]) for row in rows if row.get("date")})
                item.update({"status": "nonempty" if rows else "empty", "records": len(rows),
                             "earliest_date": dates[0] if dates else None, "latest_date": dates[-1] if dates else None,
                             "response_keys": sorted(payload.keys()) if isinstance(payload, dict) else None})
            except Exception as exc:
                item.update({"status": "error", "error": type(exc).__name__})
            output.append(item)
    return output


def _db_path_from_args(args: argparse.Namespace) -> Path:
    return resolve_db_path(getattr(args, "db_path", None), load_config())


def _stress_daily_json(row: dict[str, Any]) -> dict[str, Any]:
    """Shape stored Stress facts without exposing raw payload/provenance."""
    return {
        "date": row["date"],
        "min_stress": row["min_stress"],
        "max_stress": row["max_stress"],
        "avg_stress": row["avg_stress"],
        "distribution": {
            "relaxed": row["relax_proportion"],
            "normal": row["normal_proportion"],
            "medium": row["medium_proportion"],
            "high": row["high_proportion"],
        },
        "sample_count": row["sample_count"],
    }


def read_stress_report(
    database: Database,
    days: int,
    *,
    include_samples: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build a factual Stress report exclusively from SQLite."""
    if days < 1:
        raise ValueError("days must be at least 1")
    instant = now or datetime.now(timezone.utc)
    local_today = instant.astimezone(ZoneInfo(FRESHNESS_TIMEZONE)).date()
    from_date = (local_today - timedelta(days=days - 1)).isoformat()
    to_date = local_today.isoformat()
    daily_rows = database.fetch_stress_daily(from_date, to_date)
    latest_row = database.fetch_latest_stress_daily()
    freshness = database.factual_freshness(
        instant,
        FRESHNESS_TIMEZONE,
    )["domain_data_freshness"]["stress"]

    result = {
        "timezone": FRESHNESS_TIMEZONE,
        "from_date": from_date,
        "to_date": to_date,
        "freshness": freshness["freshness"],
        "latest": _stress_daily_json(latest_row) if latest_row else None,
        "days": [_stress_daily_json(row) for row in daily_rows],
    }
    if include_samples:
        result["samples"] = database.fetch_stress_samples(
            from_date,
            to_date,
        )
    return result


def cmd_stress(args: argparse.Namespace) -> None:
    database = Database(_db_path_from_args(args))
    try:
        result = read_stress_report(
            database,
            args.days,
            include_samples=args.samples,
        )
    finally:
        database.close()

    if args.json:
        _emit_json(result, args)
        return

    latest = result["latest"]
    print("Stress — latest")
    if latest is None:
        print("No stored Stress daily data.")
        print("Freshness: missing")
        return
    distribution = latest["distribution"]
    print(f"Date: {latest['date']}")
    print(f"Average: {latest['avg_stress']}")
    print(
        f"Min / Max: {latest['min_stress']} / "
        f"{latest['max_stress']}"
    )
    print(
        "Distribution: "
        f"Relaxed {distribution['relaxed']}%, "
        f"Normal {distribution['normal']}%, "
        f"Medium {distribution['medium']}%, "
        f"High {distribution['high']}%"
    )
    print(f"Samples: {latest['sample_count']}")
    print(f"Freshness: {result['freshness']}")
    if result["days"]:
        print("Daily history:")
        for row in result["days"]:
            print(
                f"  {row['date']}: avg {row['avg_stress']}, "
                f"min/max {row['min_stress']}/{row['max_stress']}, "
                f"samples {row['sample_count']}"
            )
    if args.samples:
        print(f"Stored samples in window: {len(result['samples'])}")


def read_sport_load_report(
    database: Database,
    days: int,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build a factual, raw-payload-free SPORT_LOAD report from SQLite."""
    if days < 1:
        raise ValueError("days must be at least 1")
    instant = now or datetime.now(timezone.utc)
    local_today = instant.astimezone(ZoneInfo(FRESHNESS_TIMEZONE)).date()
    from_date = (local_today - timedelta(days=days - 1)).isoformat()
    to_date = local_today.isoformat()
    latest = database.fetch_latest_sport_load()
    if latest is None:
        freshness = "missing"
    elif latest["date"] == to_date:
        freshness = "current"
    else:
        freshness = "stale"
    return {
        "status": "ok",
        "timezone": FRESHNESS_TIMEZONE,
        "from_date": from_date,
        "to_date": to_date,
        "freshness": freshness,
        "latest": latest,
        "days": database.fetch_sport_load(from_date, to_date),
    }


def cmd_sport_load(args: argparse.Namespace) -> None:
    database = Database(_db_path_from_args(args))
    try:
        result = read_sport_load_report(database, args.days)
    finally:
        database.close()
    if args.json:
        _emit_json(result, args)
        return
    print("SPORT_LOAD — latest")
    latest = result["latest"]
    if latest is None:
        print("No stored SPORT_LOAD data.")
        print("Freshness: missing")
        return
    print(f"Date: {latest['date']}")
    print(f"Current-day load: {latest['current_day_training_load']}")
    print(f"WTL sum: {latest['wtl_sum']}")
    print(f"Optimal range: {latest['optimal_min']}–{latest['optimal_max']}")
    print(f"Overreaching threshold: {latest['overreaching_threshold']}")
    print(f"Freshness: {result['freshness']}")
    if result["days"]:
        print("Daily history:")
        for row in result["days"]:
            print(
                f"  {row['date']}: current-day {row['current_day_training_load']}, "
                f"WTL {row['wtl_sum']}, range "
                f"{row['optimal_min']}–{row['optimal_max']}"
            )


def read_food_report(
    database: Database,
    days: int,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build a factual, raw-payload-free Food report from SQLite."""
    if days < 1:
        raise ValueError("days must be at least 1")
    instant = now or datetime.now(timezone.utc)
    local_today = instant.astimezone(ZoneInfo(FRESHNESS_TIMEZONE)).date()
    from_date = (local_today - timedelta(days=days - 1)).isoformat()
    to_date = local_today.isoformat()
    entries = database.fetch_food_entries(from_date, to_date)
    today_count = sum(row["date"] == to_date for row in entries)
    return {
        "timezone": FRESHNESS_TIMEZONE,
        "from_date": from_date,
        "to_date": to_date,
        "latest_entry_date": database.fetch_latest_food_entry_date(),
        "today_log_status": (
            "food_logged" if today_count else "no_food_logged"
        ),
        "today_entry_count": today_count,
        "entries": entries,
        "daily_totals": {
            "available": False,
            "reason": "no_native_daily_summary_implemented",
        },
    }


def cmd_food(args: argparse.Namespace) -> None:
    database = Database(_db_path_from_args(args))
    try:
        result = read_food_report(database, args.days)
    finally:
        database.close()
    if args.json:
        _emit_json(result, args)
        return
    print(f"Food / Nutrition — {result['from_date']} to {result['to_date']}")
    print(
        "Today: "
        + (
            f"{result['today_entry_count']} logged entr"
            + ("y" if result["today_entry_count"] == 1 else "ies")
            if result["today_entry_count"]
            else "no food logged"
        )
    )
    if not result["entries"]:
        print("No stored Food entries in the requested window.")
        return
    print("Entries:")
    for entry in result["entries"]:
        name = entry["food_name"] or "Unnamed food"
        print(
            f"  {entry['date']} {entry['meal_label'] or 'Unknown meal'}: "
            f"{name}"
        )
        facts = []
        if entry["measure_weight"] is not None:
            facts.append(
                f"weight={entry['measure_weight']:g}"
                + (
                    f" {entry['weight_unit']}"
                    if entry["weight_unit"]
                    else ""
                )
            )
        for key, label in (
            ("energy", "energy"),
            ("carbohydrates", "carbohydrates"),
            ("protein", "protein"),
            ("fat_total", "fat"),
        ):
            if entry[key] is not None:
                facts.append(f"{label}={entry[key]:g}")
        if facts:
            print("    " + ", ".join(facts))
    print("Daily totals: no native daily summary exposed")


def cmd_sync_db(args: argparse.Namespace) -> None:
    lock = SyncLock(args.lock_path) if getattr(args, "lock_path", None) else None
    if lock is not None and not lock.acquire(nonblocking=True):
        message = {"status": "skipped", "reason": "lock_held", "lock_path": str(args.lock_path)}
        if args.json:
            _emit_json(message, args)
        else:
            print(f"Synchronization skipped: lock held ({args.lock_path})")
        return
    database: Database | None = None
    try:
        database = Database(_db_path_from_args(args))
        result = sync_native_metrics(_load_client(), database, args.days, limit=args.limit)
    finally:
        if database is not None:
            database.close()
        if lock is not None:
            lock.release()
    if args.json:
        _emit_json(result, args)
        if result["status"] == "error":
            raise SystemExit(2)
        return
    print(f"SQLite synchronization: {result['status']}")
    print(f"Database: {result['database_path']}")
    print(f"Range: {result['from_ms']} .. {result['to_ms']}")
    for domain in result["domains"]:
        print(
            f"  {domain['domain']}: {domain['status']} "
            f"retrieved={domain['records_retrieved']} inserted={domain['inserted']} "
            f"updated={domain['updated']} unchanged={domain['unchanged']}"
            + (f" error={domain['error']}" if domain.get("error") else "")
        )
    print(f"Duration: {result['duration_seconds']:.3f}s")
    if result["status"] == "error":
        raise SystemExit(2)


def cmd_backfill(args: argparse.Namespace) -> None:
    lock = SyncLock(args.lock_path) if getattr(args, "lock_path", None) else None
    if lock is not None and not lock.acquire(nonblocking=True):
        result = {"status": "skipped", "reason": "lock_held", "lock_path": str(args.lock_path)}
        _emit_json(result, args) if args.json else print(f"Backfill skipped: lock held ({args.lock_path})")
        return
    database: Database | None = None
    try:
        database = Database(_db_path_from_args(args))
        result = backfill_native_metrics(_load_client(), database, args.days, limit=args.limit, chunk_days=args.chunk_days)
    finally:
        if database is not None:
            database.close()
        if lock is not None:
            lock.release()
    if args.json:
        _emit_json(result, args)
        return
    print(f"Historical synchronization: {result['status']} ({result['requested_days']} days)")
    for domain in result["domains"]:
        print(f"  {domain['domain']}: {domain['status']} chunks={domain['chunks_completed']} "
              f"retrieved={domain['records_retrieved']} inserted={domain['inserted']} "
              f"updated={domain['updated']} unchanged={domain['unchanged']}"
              + (f" error={domain['error']}" if domain.get("error") else ""))


def cmd_probe_history(args: argparse.Namespace) -> None:
    result = probe_historical_domains(_load_client(), args.probe_days, limit=args.limit)
    _emit_json(result, args)


def cmd_db_status(args: argparse.Namespace) -> None:
    database = Database(_db_path_from_args(args))
    try:
        result = database.status()
    finally:
        database.close()
    if args.json:
        _emit_json(result, args)
        return
    print(f"Database: {result['database_path']}")
    print(f"Schema version: {result['schema_version']}")
    print(f"Date range: {result['date_range']['from']} .. {result['date_range']['to']}")
    print(f"Latest sync: {result['latest_sync_at'] or '—'}")
    print("Record counts:")
    for table, count in result["record_counts"].items():
        print(f"  {table}: {count}")


def _database_operation_error(exc: Exception) -> None:
    # Keep operational errors concise and independent of API/config contents.
    raise SystemExit(f"Database operation failed: {type(exc).__name__}") from None


def cmd_db_check(args: argparse.Namespace) -> None:
    try:
        result = inspect_database_file(_db_path_from_args(args))
    except (OSError, sqlite3.DatabaseError, ValueError) as exc:
        _database_operation_error(exc)
    if args.json:
        _emit_json(result, args)
        return
    print(f"Database: {result['database_path']}")
    print(f"Integrity: {result['integrity_check']}")
    print(f"Foreign keys: {'ok' if not result['foreign_key_check'] else 'violations'}")
    print(f"Schema version: {result['schema_version']}")
    print(f"Journal mode: {result['journal_mode']}")
    print(f"Size: {result['database_size_bytes']} bytes")
    print(f"Latest sync: {result['latest_sync'] or '—'}")


def cmd_db_backup(args: argparse.Namespace) -> None:
    try:
        result = backup_database(_db_path_from_args(args), args.output, args.overwrite)
    except (OSError, sqlite3.DatabaseError, ValueError) as exc:
        _database_operation_error(exc)
    if args.json:
        _emit_json(result, args)
        return
    print(f"Backup created: {result['output_path']}")
    print(f"Size: {result['database_size_bytes']} bytes")
    print(f"Schema version: {result['schema_version']}")
    print(f"Integrity: {result['integrity_check']}")


def cmd_db_restore(args: argparse.Namespace) -> None:
    if not args.db_path:
        raise SystemExit("Database operation failed: --db target is required")
    try:
        result = restore_database(args.input, args.db_path, args.overwrite)
    except (OSError, sqlite3.DatabaseError, ValueError) as exc:
        _database_operation_error(exc)
    if args.json:
        _emit_json(result, args)
        return
    print(f"Restore created: {result['output_path']}")
    print(f"Size: {result['database_size_bytes']} bytes")
    print(f"Schema version: {result['schema_version']}")
    print(f"Integrity: {result['integrity_check']}")
    print(f"Counts match: {result['counts_match']}")


def cmd_sync_health(args: argparse.Namespace) -> None:
    try:
        info = inspect_database_file(_db_path_from_args(args))
        connection = sqlite3.connect(_db_path_from_args(args), timeout=30.0)
        try:
            successful = connection.execute(
                "SELECT finished_at FROM sync_runs WHERE status='ok' AND finished_at IS NOT NULL ORDER BY finished_at DESC LIMIT 1"
            ).fetchone()
            failed = connection.execute(
                "SELECT finished_at, status FROM sync_runs WHERE status != 'ok' AND finished_at IS NOT NULL ORDER BY finished_at DESC LIMIT 1"
            ).fetchone()
            latest = connection.execute(
                "SELECT finished_at, status, summary_json FROM sync_runs WHERE finished_at IS NOT NULL ORDER BY finished_at DESC LIMIT 1"
            ).fetchone()
        finally:
            connection.close()
        now = datetime.now(timezone.utc)
        last_success_at = successful[0] if successful else None
        age_seconds = None
        if last_success_at:
            age_seconds = max(0.0, (now - datetime.fromisoformat(last_success_at).astimezone(timezone.utc)).total_seconds())
        lock_path = Path(args.lock_path or "run/zepp-health-sync.lock")
        held = lock_is_held(lock_path)
        duration = None
        if latest and latest[2]:
            try:
                duration = json.loads(latest[2]).get("duration_seconds")
            except (TypeError, ValueError, json.JSONDecodeError):
                duration = None
        result = {
            "database_path": info["database_path"],
            "schema_version": info["schema_version"],
            "integrity_status": info["integrity_check"],
            "foreign_key_status": "ok" if not info["foreign_key_check"] else "violations",
            "latest_sync": {"finished_at": latest[0], "status": latest[1]} if latest else None,
            "latest_successful_sync": last_success_at,
            "latest_failed_sync": {"finished_at": failed[0], "status": failed[1]} if failed else None,
            "last_success_age_seconds": age_seconds,
            "lock_path": str(lock_path),
            "lock_held": held,
            "database_size_bytes": info["database_size_bytes"],
            "last_sync_duration_seconds": duration,
        }
        database = Database(_db_path_from_args(args))
        try:
            result["factual_freshness"] = database.factual_freshness()
        finally:
            database.close()
        if info["integrity_check"] != "ok" or info["foreign_key_check"]:
            exit_code = 2
        elif not successful:
            exit_code = 2
        elif age_seconds is not None and age_seconds > 12 * 3600:
            exit_code = 1
        elif latest and latest[1] != "ok":
            exit_code = 1
        else:
            exit_code = 0
    except (OSError, sqlite3.DatabaseError, ValueError, RuntimeError) as exc:
        if args.json:
            _emit_json({"status": "configuration_or_database_error", "error": type(exc).__name__}, args)
        else:
            print(f"Sync health: configuration/database error ({type(exc).__name__})")
        raise SystemExit(3) from None
    result["status"] = "healthy" if exit_code == 0 else "warning" if exit_code == 1 else "failed"
    if args.json:
        _emit_json(result, args)
    else:
        print(f"Sync health: {result['status']}")
        print(f"Database: {result['database_path']}")
        print(f"Integrity: {result['integrity_status']}")
        print(f"Latest successful sync: {result['latest_successful_sync'] or '—'}")
        print(f"Last success age: {result['last_success_age_seconds'] if result['last_success_age_seconds'] is not None else '—'} seconds")
        print(f"Lock held: {result['lock_held']} ({result['lock_path']})")
        print(f"Last duration: {result['last_sync_duration_seconds'] if result['last_sync_duration_seconds'] is not None else '—'} seconds")
    if exit_code:
        raise SystemExit(exit_code)


def cmd_temperature(args: argparse.Namespace) -> None:
    c = _load_client()
    et, st = _EVENT_PRESETS["temperature"]
    from_ms, to_ms = _ms_window(args.days)
    data = c.events(et, st, from_ms, to_ms, limit=args.limit)
    if args.raw or getattr(args, "json", False):
        _emit_json(data, args)
        return
    items = data.get("items") or []
    if not items:
        print(f"No skin-temperature samples in the last {args.days} day(s).")
        return
    print("Skin temperature (calibrated delta from baseline, hundredths of \u00b0C):\n")
    print(f"  {'date (UTC)':<19}  {'delta':>7}  {'score':>5}  {'baseline':>8}  device")
    for it in items:
        ts = it.get("timestamp", 0) / 1000
        v = it.get("value") or {}
        cal = v.get("skinTempCalibrated")
        score = v.get("skinTempScore")
        base = v.get("skinTempBaseLine")
        dev = v.get("deviceId", "?")
        when = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        cal_disp = "—" if cal in (None, 255) else f"{cal/100:+.2f}\u00b0C"
        score_disp = "—" if score in (None, 255) else str(score)
        base_disp = "—" if base in (None, -10, 255) else str(base)
        print(f"  {when:<19}  {cal_disp:>7}  {score_disp:>5}  {base_disp:>8}  {dev}")
    print(
        "\nNote: Zepp/Huami report skin temperature as a calibrated delta from your "
        "personal baseline, not absolute body temperature."
    )


def cmd_summary(args: argparse.Namespace) -> None:
    """Short human-readable snapshot: training load + empty checks for HR/weight."""
    c = _load_client()
    end = _today_utc()
    start = end - timedelta(days=args.days - 1)
    load = c.sport_load(start, end)
    items = load.get("items") or []
    hr = c.heart_rate(
        int((datetime.now(timezone.utc) - timedelta(days=7)).timestamp()),
        int(datetime.now(timezone.utc).timestamp()),
    )
    w = c.weight_records(
        int((datetime.now(timezone.utc) - timedelta(days=30)).timestamp()),
        int(datetime.now(timezone.utc).timestamp()),
    )
    if getattr(args, "json", False):
        payload = {
            "training_load": load,
            "training_load_range_days": args.days,
            "heart_rate_last_7_days": hr,
            "weight_last_30_days": w,
        }
        _emit_json(payload, args)
        return
    print(f"Training load ({len(items)} days in range)\n")
    for row in items:
        day = row.get("dayId", "?")
        wtl = row.get("wtlSum")
        train = row.get("currnetDayTrainLoad")
        print(f"  {day}  load={wtl}  day_train_load={train}")
    print()
    print(f"Heart rate samples (last 7d): {len((hr.get('items') or []))} items")
    print(f"Weight records (last 30d): {len((w.get('items') or []))} items")


def main() -> None:
    p = argparse.ArgumentParser(description="Zepp health API helper")
    p.add_argument(
        "--days",
        type=int,
        default=None,
        help="Default days of history (overridable per subcommand)",
    )
    p.add_argument(
        "--config",
        help="Path to config.json (default: ./config.json or ~/.config/zepp/config.json)",
    )
    p.add_argument(
        "--db",
        dest="db_path",
        default=None,
        help="SQLite database path (highest priority; default data/zepp_health.db)",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    def _add_days(parser: argparse.ArgumentParser) -> None:
        # SUPPRESS keeps the parent's --days when the user only sets it globally.
        parser.add_argument(
            "--days",
            type=int,
            default=argparse.SUPPRESS,
            help="Days of history (default 14)",
        )

    def _add_json(parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--json",
            action="store_true",
            help="Output compact JSON on one line (easy to pipe to jq)",
        )

    def _add_db(parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--db",
            dest="db_path",
            default=argparse.SUPPRESS,
            help="SQLite database path (overrides config/env/default)",
        )

    sp = sub.add_parser(
        "phn-record",
        help="Read-only Zepp Coach PHN daily record probe",
    )
    _add_days(sp)
    _add_json(sp)
    sp.add_argument("--limit", type=int, default=2000)
    sp.set_defaults(func=cmd_phn_record)

    sp = sub.add_parser(
        "phn-training-plan",
        help="Read-only Zepp Coach PHN training-plan state probe",
    )
    _add_days(sp)
    _add_json(sp)
    sp.add_argument("--limit", type=int, default=200)
    sp.set_defaults(func=cmd_phn_training_plan)

    sp = sub.add_parser(
        "sync-phn",
        help=(
            "Opt-in PHN synchronization; kept separate from sync-db "
            "until live GET behavior is validated"
        ),
    )
    _add_days(sp)
    _add_json(sp)
    _add_db(sp)
    sp.add_argument("--limit", type=int, default=2000)
    sp.set_defaults(func=cmd_sync_phn)

    sp = sub.add_parser(
        "sport-load",
        help="Factual SPORT_LOAD daily data from SQLite",
    )
    _add_days(sp)
    _add_json(sp)
    _add_db(sp)
    sp.set_defaults(func=cmd_sport_load)

    sp = sub.add_parser("vo2", help="VO2 max series (may be empty)")
    _add_days(sp)
    _add_json(sp)
    sp.set_defaults(func=cmd_vo2)

    sp = sub.add_parser("heart-rate", help="Heart rate samples")
    _add_days(sp)
    _add_json(sp)
    sp.set_defaults(func=cmd_heart_rate)

    sp = sub.add_parser("weight", help="Weight records")
    _add_days(sp)
    _add_json(sp)
    sp.set_defaults(func=cmd_weight)

    sp = sub.add_parser(
        "band-data",
        help="Sleep/steps raw band sync (/v1/data/band_data.json)",
    )
    _add_days(sp)
    _add_json(sp)
    sp.add_argument(
        "--query-type",
        dest="query_type",
        choices=("detail", "summary"),
        default="detail",
        help="detail = per-day data; summary = yearly chunks (can be huge)",
    )
    sp.add_argument(
        "--from-date",
        help="Start date YYYY-MM-DD (use with --to-date instead of --days)",
    )
    sp.add_argument("--to-date", help="End date YYYY-MM-DD")
    sp.set_defaults(func=cmd_band_data)

    sp = sub.add_parser("manual-data", help="Manual entries (/v1/user/manualData.json)")
    _add_json(sp)
    sp.add_argument(
        "--type",
        default="sleep",
        help="Record type (default sleep)",
    )
    sp.set_defaults(func=cmd_manual_data)

    sp = sub.add_parser("user-info", help="Profile blob (huami.health.getUserInfo.json)")
    _add_json(sp)
    sp.set_defaults(func=cmd_user_info)

    sp = sub.add_parser("blood-pressure", help="Blood pressure (/users/me/bloodPressure)")
    _add_json(sp)
    sp.add_argument(
        "--bp-days",
        type=int,
        default=7,
        dest="bp_days",
        metavar="N",
        help="Days to include (default 7)",
    )
    sp.add_argument(
        "--to-date",
        help="Anchor date YYYY-MM-DD (default today UTC)",
    )
    sp.set_defaults(func=cmd_blood_pressure)

    sp = sub.add_parser(
        "user-events",
        help="User timeline (/users/{id}/events): stress, PAI, SpO₂, …",
    )
    _add_days(sp)
    _add_json(sp)
    sp.add_argument("--type", help="eventType (if no --preset)")
    sp.add_argument("--subtype", help="subType (optional)")
    sp.add_argument(
        "--preset",
        choices=sorted(USER_EVENT_PRESETS.keys()),
        help="Shortcut for common eventType/subType pairs",
    )
    sp.add_argument("--limit", type=int, default=2000)
    sp.add_argument(
        "--reverse",
        action="store_true",
        help="reverse=1 (newest first)",
    )
    sp.set_defaults(func=cmd_user_events)

    sp = sub.add_parser(
        "user-events-day",
        help="User events with ISO window (/users/{id}/events/dateString), e.g. SpO₂ ODI",
    )
    _add_json(sp)
    sp.add_argument("--start", required=True, help="from ISO datetime, e.g. 2026-04-18T00:00:00")
    sp.add_argument("--end", required=True, help="to ISO datetime")
    sp.add_argument(
        "--timezone",
        help="IANA zone (default: config timezone or UTC)",
    )
    sp.add_argument("--type", help="eventType (if no --preset)")
    sp.add_argument("--subtype", help="subType (if no --preset)")
    sp.add_argument(
        "--preset",
        choices=sorted(USER_EVENT_DAY_PRESETS.keys()),
        help="spo2-odi or spo2-osa",
    )
    sp.add_argument("--limit", type=int, default=999)
    sp.add_argument("--reverse", action="store_true")
    sp.set_defaults(func=cmd_user_events_day)

    sp = sub.add_parser(
        "second-hr",
        help="Per-second HR file index (/users/me/fileInfo/events)",
    )
    _add_days(sp)
    _add_json(sp)
    sp.add_argument("--limit", type=int, default=200)
    sp.set_defaults(func=cmd_second_hr)

    sp = sub.add_parser(
        "run-history",
        help="Workout history for UTC midnight window (/v1/sport/{sport}/history.json)",
    )
    _add_json(sp)
    sp.add_argument(
        "--sport",
        default="run",
        help="URL segment: run, walking, ride, swimming, … (default run)",
    )
    sp.set_defaults(func=cmd_run_history)

    sp = sub.add_parser(
        "diagnose-activities",
        help="Sanitized structural audit of sport-specific Zepp workout history",
    )
    _add_json(sp)
    sp.add_argument("--from-date", required=True, help="First local date YYYY-MM-DD")
    sp.add_argument("--to-date", required=True, help="Last local date YYYY-MM-DD")
    sp.add_argument(
        "--timezone",
        help="IANA timezone for local bounds (default config or Europe/Ljubljana)",
    )
    sp.add_argument(
        "--sport",
        action="append",
        required=True,
        help="Exact /v1/sport/{sport}/ URL segment; repeat for multiple candidates",
    )
    sp.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Maximum records reported per sport; this is not a server query parameter",
    )
    sp.add_argument(
        "--track-id",
        help="Report only the exact trackid/trackId value (filter applied locally)",
    )
    sp.add_argument(
        "--need-sub-data",
        type=int,
        choices=(0, 1),
        default=1,
        help="Request Zepp sub-data (default 1)",
    )
    sp.add_argument(
        "--compare-sub-data",
        action="store_true",
        help=(
            "Request need_sub_data=0 and 1 and compare one --track-id safely; "
            "--include-text is ignored"
        ),
    )
    sp.add_argument(
        "--include-text",
        action="store_true",
        help="Explicitly include title/description/note values; omitted by default",
    )
    sp.set_defaults(func=cmd_diagnose_activities)

    sp = sub.add_parser(
        "diagnose-activity-detail",
        help=(
            "Sanitized probe of the public-code-backed activity detail contract"
        ),
    )
    _add_json(sp)
    sp.add_argument("--from-date", required=True, help="First local date YYYY-MM-DD")
    sp.add_argument("--to-date", required=True, help="Last local date YYYY-MM-DD")
    sp.add_argument(
        "--timezone",
        help="IANA timezone for local bounds (default config or Europe/Ljubljana)",
    )
    sp.add_argument(
        "--track-id",
        required=True,
        help="Exact activity trackid; source is discovered from bounded history",
    )
    sp.set_defaults(func=cmd_diagnose_activity_detail)

    sp = sub.add_parser(
        "diagnose-canonical-activity",
        help="Build one privacy-safe canonical history/detail activity",
    )
    _add_json(sp)
    sp.add_argument("--from-date", required=True, help="First local date YYYY-MM-DD")
    sp.add_argument("--to-date", required=True, help="Last local date YYYY-MM-DD")
    sp.add_argument(
        "--timezone",
        help="IANA timezone for local bounds (default config or Europe/Ljubljana)",
    )
    sp.add_argument(
        "--track-id",
        required=True,
        help="Exact activity trackid; source is discovered but never printed",
    )
    sp.set_defaults(func=cmd_diagnose_canonical_activity)

    sp = sub.add_parser(
        "diagnose-sport-coverage",
        help="Bounded privacy-safe inventory of activity types from run history",
    )
    _add_json(sp)
    sp.add_argument("--from-date", required=True, help="First local date YYYY-MM-DD")
    sp.add_argument("--to-date", required=True, help="Last local date YYYY-MM-DD")
    sp.add_argument(
        "--timezone",
        help="IANA timezone for local bounds (default config or Europe/Ljubljana)",
    )
    sp.add_argument(
        "--need-sub-data",
        type=int,
        choices=(0, 1),
        default=1,
        help="Request Zepp sub-data (default 1)",
    )
    sp.add_argument(
        "--mapping-list",
        action="store_true",
        help=(
            "Print one privacy-safe representative line per type/sport_mode "
            "instead of JSON"
        ),
    )
    sp.set_defaults(func=cmd_diagnose_sport_coverage)

    sp = sub.add_parser(
        "diagnose-sport-capabilities",
        help="Sanitized capability audit of 14 approved production fixtures",
    )
    _add_json(sp)
    sp.add_argument("--from-date", default="2026-01-01")
    sp.add_argument("--to-date", default="2026-07-26")
    sp.add_argument(
        "--timezone",
        help="IANA timezone for local bounds (default config or Europe/Ljubljana)",
    )
    sp.add_argument(
        "--need-sub-data",
        type=int,
        choices=(0, 1),
        default=1,
        help="Request Zepp sub-data (default 1)",
    )
    sp.set_defaults(func=cmd_diagnose_sport_capabilities)

    sp = sub.add_parser("summary", help="Brief text summary")
    _add_days(sp)
    _add_json(sp)
    sp.set_defaults(func=cmd_summary)

    sp = sub.add_parser(
        "insights",
        help="Normalized Charge/insight_data records",
    )
    _add_days(sp)
    _add_json(sp)
    sp.add_argument("--csv", metavar="PATH", help="Export one row per sample to CSV")
    sp.add_argument("--limit", type=int, default=2000)
    sp.set_defaults(func=cmd_insights)

    sp = sub.add_parser("hrv", help="Zepp HRVRMSSD real_data samples")
    _add_days(sp)
    _add_json(sp)
    sp.add_argument("--limit", type=int, default=2000)
    sp.set_defaults(func=cmd_hrv)

    sp = sub.add_parser("wake-energy", help="Zepp Charge wake_data metrics")
    _add_days(sp)
    _add_json(sp)
    sp.add_argument("--limit", type=int, default=2000)
    sp.set_defaults(func=cmd_wake_energy)

    sp = sub.add_parser(
        "diagnose-wake-energy",
        help="Sanitized Charge/wake_data raw-versus-normalized date report",
    )
    _add_json(sp)
    _add_db(sp)
    sp.add_argument("--from-date", required=True, help="First local date YYYY-MM-DD")
    sp.add_argument("--to-date", required=True, help="Last local date YYYY-MM-DD")
    sp.add_argument(
        "--timezone",
        help="IANA timezone for request bounds/display (default config or Europe/Ljubljana)",
    )
    sp.add_argument("--limit", type=int, default=200)
    sp.set_defaults(func=cmd_diagnose_wake_energy)

    sp = sub.add_parser("exertion", help="Zepp exertion algo_result metrics")
    _add_days(sp)
    _add_json(sp)
    sp.add_argument("--limit", type=int, default=2000)
    sp.set_defaults(func=cmd_exertion)

    sp = sub.add_parser("lifeload", help="Zepp LifeLoad summary values")
    _add_days(sp)
    _add_json(sp)
    sp.add_argument("--limit", type=int, default=2000)
    sp.set_defaults(func=cmd_lifeload)

    sp = sub.add_parser("charge-data", help="Zepp Charge real_data samples")
    _add_days(sp)
    _add_json(sp)
    sp.add_argument("--limit", type=int, default=2000)
    sp.set_defaults(func=cmd_charge_data)

    sp = sub.add_parser("daily-status", help="Consolidated Zepp-native daily metrics")
    _add_days(sp)
    _add_json(sp)
    _add_db(sp)
    sp.add_argument("--from-db", action="store_true", help="Read stored data without contacting Zepp")
    sp.add_argument("--limit", type=int, default=2000)
    sp.set_defaults(func=cmd_daily_status)

    sp = sub.add_parser(
        "stress",
        help="Factual Stress daily data from SQLite",
    )
    _add_days(sp)
    _add_json(sp)
    _add_db(sp)
    sp.add_argument(
        "--samples",
        action="store_true",
        help="Include stored native sparse Stress samples",
    )
    sp.set_defaults(func=cmd_stress)

    sp = sub.add_parser(
        "food",
        help="Factual Food/Nutrition entries from SQLite",
    )
    _add_days(sp)
    _add_json(sp)
    _add_db(sp)
    sp.set_defaults(func=cmd_food)

    sp = sub.add_parser("sync-db", help="Fetch native Zepp metrics into SQLite")
    _add_days(sp)
    _add_json(sp)
    _add_db(sp)
    sp.add_argument("--limit", type=int, default=2000)
    sp.add_argument("--lock-path", help="Advisory lock path for unattended synchronization")
    sp.set_defaults(func=cmd_sync_db)

    sp = sub.add_parser(
        "sync-activities",
        help="Incrementally persist bounded native Zepp history/detail activities",
    )
    _add_json(sp)
    _add_db(sp)
    sp.add_argument(
        "--days",
        type=int,
        help="Inclusive local-day window ending today (default 7)",
    )
    sp.add_argument("--from-date", help="First local date YYYY-MM-DD")
    sp.add_argument("--to-date", help="Last local date YYYY-MM-DD")
    sp.add_argument(
        "--timezone",
        help="IANA timezone for request bounds (default config or Europe/Ljubljana)",
    )
    sp.add_argument(
        "--max-activities",
        type=int,
        default=50,
        help="Hard per-run activity limit (default 50)",
    )
    sp.add_argument(
        "--refresh-details",
        action="store_true",
        help="Refetch detail even when the stored history summary is unchanged",
    )
    sp.add_argument(
        "--lock-path",
        help="Optional advisory lock path for unattended activity synchronization",
    )
    sp.set_defaults(func=cmd_sync_activities)

    sp = sub.add_parser(
        "activity-status",
        help="Privacy-safe native activity database and sync status",
    )
    _add_json(sp)
    _add_db(sp)
    sp.set_defaults(func=cmd_activity_status)

    sp = sub.add_parser(
        "inspect-activity",
        help="Inspect one stored activity without coordinates or raw samples",
    )
    _add_json(sp)
    _add_db(sp)
    sp.add_argument("--track-id", required=True)
    sp.add_argument(
        "--include-notes",
        action="store_true",
        help="Explicitly include locally stored Workout Notes text",
    )
    sp.set_defaults(func=cmd_inspect_activity)

    sp = sub.add_parser("backfill", help="Resumable backwards historical synchronization")
    sp.add_argument("--days", type=int, required=True, help="Historical calendar days to request")
    _add_json(sp)
    _add_db(sp)
    sp.add_argument("--limit", type=int, default=2000)
    sp.add_argument("--chunk-days", type=int, default=30, help="Calendar days per API request (default 30)")
    sp.add_argument("--lock-path", help="Advisory lock path for unattended synchronization")
    sp.set_defaults(func=cmd_backfill)

    sp = sub.add_parser("probe-history", help="Probe historical coverage and date-window behavior")
    _add_json(sp)
    sp.add_argument("--probe-days", type=int, nargs="+", default=[7, 30, 90, 180, 365, 730],
                    help="Requested ranges to compare (default: 7 30 90 180 365 730)")
    sp.add_argument("--limit", type=int, default=2000)
    sp.set_defaults(func=cmd_probe_history)

    sp = sub.add_parser("db-status", help="Inspect local Zepp SQLite database")
    _add_json(sp)
    _add_db(sp)
    sp.set_defaults(func=cmd_db_status)

    sp = sub.add_parser("db-check", help="Run SQLite integrity and foreign-key checks")
    _add_json(sp)
    _add_db(sp)
    sp.set_defaults(func=cmd_db_check)

    sp = sub.add_parser("db-backup", help="Create a consistent SQLite backup")
    _add_json(sp)
    _add_db(sp)
    sp.add_argument("--output", required=True, help="Backup file path")
    sp.add_argument("--overwrite", action="store_true", help="Replace an existing backup explicitly")
    sp.set_defaults(func=cmd_db_backup)

    sp = sub.add_parser("db-restore", help="Restore a SQLite backup to a new path")
    _add_json(sp)
    _add_db(sp)
    sp.add_argument("--input", required=True, help="SQLite backup file path")
    sp.add_argument("--overwrite", action="store_true", help="Replace an existing target explicitly")
    sp.set_defaults(func=cmd_db_restore)

    sp = sub.add_parser("sync-health", help="Report synchronization and database health")
    _add_json(sp)
    _add_db(sp)
    sp.add_argument("--lock-path", help="Lock path to inspect (default run/zepp-health-sync.lock)")
    sp.set_defaults(func=cmd_sync_health)

    sp = sub.add_parser("readiness", help="Zepp readiness/watch_score values")
    _add_days(sp)
    _add_json(sp)
    sp.add_argument("--limit", type=int, default=2000)
    sp.add_argument(
        "--latest-per-day",
        action="store_true",
        help="Select the latest readiness/watch_score record per date",
    )
    sp.set_defaults(func=cmd_readiness)

    sp = sub.add_parser("sleep-status", help="Sleep-related Zepp readiness/watch_score values")
    _add_days(sp)
    _add_json(sp)
    sp.add_argument("--limit", type=int, default=2000)
    sp.set_defaults(func=cmd_sleep_status)

    sp = sub.add_parser("event-domains", help="Probe known Zepp eventType/subType domains")
    _add_days(sp)
    _add_json(sp)
    sp.add_argument("--limit", type=int, default=2000)
    sp.set_defaults(func=cmd_event_domains)

    sp = sub.add_parser(
        "temperature",
        help="Skin temperature (delta from baseline) from readiness/watch_score",
    )
    _add_days(sp)
    _add_json(sp)
    sp.add_argument("--limit", type=int, default=200)
    sp.add_argument(
        "--raw",
        action="store_true",
        help="Same as --json: raw API response JSON (alias for backwards compatibility)",
    )
    sp.set_defaults(func=cmd_temperature)

    sp = sub.add_parser(
        "events",
        help="Generic /v2/users/me/events (use --type and --subtype, or --preset)",
    )
    _add_days(sp)
    _add_json(sp)
    sp.add_argument("--type", help="eventType (e.g. readiness, Charge, hrv_sdnn)")
    sp.add_argument("--subtype", help="subType (e.g. watch_score, real_data)")
    sp.add_argument(
        "--preset",
        choices=sorted(_EVENT_PRESETS.keys()),
        help="convenience shortcut that fills --type/--subtype",
    )
    sp.add_argument("--limit", type=int, default=200)

    def _events_dispatch(a: argparse.Namespace) -> None:
        if a.preset:
            a.type, a.subtype = _EVENT_PRESETS[a.preset]
        if not a.type or not a.subtype:
            sys.exit("Provide --preset, or both --type and --subtype.")
        cmd_events(a)

    sp.set_defaults(func=_events_dispatch)

    init_p = sub.add_parser(
        "init",
        help="Extract apptoken/user_id/host from a proxy capture (HAR or JSON) into config.json",
    )
    init_p.add_argument("capture", help="Path to a proxy capture (HAR or JSON session export)")
    init_p.add_argument("-o", "--output", help="Where to write config.json (default ./config.json)")
    init_p.add_argument("-f", "--force", action="store_true", help="Overwrite without merging")
    init_p.set_defaults(func=cmd_init)

    cfg_p = sub.add_parser("config", help="Show current config (token masked) or search paths")
    cfg_p.add_argument("--show", action="store_true", help="print resolved config (default)")
    cfg_p.add_argument("--path", action="store_true", help="print config search paths")
    cfg_p.set_defaults(func=cmd_config)

    args = p.parse_args()
    global _CONFIG_PATH_OVERRIDE
    _CONFIG_PATH_OVERRIDE = args.config
    # sync-activities distinguishes an omitted --days from an explicit value
    # so its date-range validation can allow --from-date/--to-date.
    if getattr(args, "days", None) is None and args.cmd != "sync-activities":
        args.days = 14
    if args.cmd == "config" and not (args.show or args.path):
        args.show = True
    args.func(args)


if __name__ == "__main__":
    main()
