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

    def sport_load(self, start_day: date, end_day: date) -> Any:
        return self.get_json(
            f"/v2/watch/users/{self.user_id}/WatchSportStatistics/SPORT_LOAD",
            {
                "startDay": start_day.isoformat(),
                "endDay": end_day.isoformat(),
                "limit": 900,
                "isReverse": "true",
            },
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


def cmd_sport_load(args: argparse.Namespace) -> None:
    c = _load_client()
    end = _today_utc()
    start = end - timedelta(days=args.days - 1)
    data = c.sport_load(start, end)
    _emit_json(data, args)


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
    "distance_ascend", "totalClimbDistance", "swim_pool_length",
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
)
ACTIVITY_UNPROVEN_NEGATIVE_CANDIDATES = {-1, -100, -20000, -274}
ACTIVITY_FIXTURE_MAPPINGS = {
    22: {
        "sport_family": "Hike",
        "confidence": "PROVEN_FOR_FIXTURE",
        "evidence": "2026-07-25 Ojstrica production fixture",
    },
    130: {
        "sport_family": "Cross-training",
        "confidence": "PROVEN_FOR_FIXTURE",
        "evidence": "2026-07-22 production fixture",
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
    if (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and value in ACTIVITY_UNPROVEN_NEGATIVE_CANDIDATES
    ):
        return "UNKNOWN_SEMANTICS"
    return "PRESENT_WITH_VALUE"


def _activity_response_next(data: Any) -> Any:
    if isinstance(data, dict) and isinstance(data.get("data"), dict):
        return data["data"].get("next")
    return None


def inventory_activity_payload(
    data: Any, *, sport_segment: str = "run"
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
        except (TypeError, ValueError):
            numeric_type = None
        type_groups.append({
            "type": representative.get("type"),
            "sport_mode": representative.get("sport_mode"),
            "record_count": len(members),
            "representative_trackid": representative.get("trackid"),
            "known_mapping": ACTIVITY_FIXTURE_MAPPINGS.get(numeric_type),
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


def cmd_diagnose_sport_coverage(args: argparse.Namespace) -> None:
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
        inventory = inventory_activity_payload(payload, sport_segment="run")
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
    rows = _normalize_value_records(
        c.events("exertion", "algo_result", from_ms, to_ms, limit=args.limit),
        "exertion", "algo_result",
        ("recoveryFactor", "totalScore", "activityScore", "exerciseScore", "atl", "ctl", "tsb"),
    )
    if args.json:
        _emit_json(rows, args)
    else:
        _print_rows("Zepp exertion / algorithm results", rows, ("date", "recoveryFactor", "totalScore", "activityScore", "exerciseScore", "atl", "ctl", "tsb"))


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
        ("exertion", exertion_rows, ("recoveryFactor", "totalScore", "activityScore", "exerciseScore", "atl", "ctl", "tsb")),
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
    exertion = _normalize_value_records(c.events("exertion", "algo_result", from_ms, to_ms, limit=args.limit), "exertion", "algo_result", ("recoveryFactor", "totalScore", "activityScore", "exerciseScore", "atl", "ctl", "tsb"))
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
    ("exertion", "exertion", "algo_result", lambda data: _normalize_value_records(data, "exertion", "algo_result", ("recoveryFactor", "totalScore", "activityScore", "exerciseScore", "atl", "ctl", "tsb"))),
    ("readiness", "readiness", "watch_score", normalize_readiness_data),
    ("charge", "Charge", "real_data", normalize_charge_data),
    ("insights", "Charge", "insight_data", lambda data: _insight_rows(normalize_insight_data(data))),
    ("lifeload", "LifeLoad", "summary", lambda data: _normalize_value_records(data, "LifeLoad", "summary", ("lifeLoad",), confidence="candidate")),
)


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

    sp = sub.add_parser("sport-load", help="Daily training load (WatchSportStatistics)")
    _add_days(sp)
    _add_json(sp)
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
    sp.set_defaults(func=cmd_diagnose_sport_coverage)

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

    sp = sub.add_parser("sync-db", help="Fetch native Zepp metrics into SQLite")
    _add_days(sp)
    _add_json(sp)
    _add_db(sp)
    sp.add_argument("--limit", type=int, default=2000)
    sp.add_argument("--lock-path", help="Advisory lock path for unattended synchronization")
    sp.set_defaults(func=cmd_sync_db)

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
    if getattr(args, "days", None) is None:
        args.days = 14
    if args.cmd == "config" and not (args.show or args.path):
        args.show = True
    args.func(args)


if __name__ == "__main__":
    main()
