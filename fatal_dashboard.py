"""Fatal-error dashboard: Kibana links, log paths and a 24h pivot of FATAL errors.

Sources are configured in log_sources.json (override with LOG_SOURCES_FILE).
Each source gives a Kibana index pattern / data view and a log-file glob.
"""
import glob
import json
import os
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable
from urllib.parse import quote

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse

CONFIG_FILE = Path(os.environ.get("LOG_SOURCES_FILE", "log_sources.json"))
WINDOW_HOURS = 24

# Leading timestamp, e.g. "2026-11-11 10:15:32,123" or "2026-11-11T10:15:32.123Z"
TS_RE = re.compile(r"^\[?(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}:\d{2})")
FATAL_RE = re.compile(r"\bFATAL\b")
CODE_RE = re.compile(r"\b([A-Z]{1,5}\d{3,6})\b")

router = APIRouter()

# main.py plugs in a DB-backed lookup; default returns no hints.
hint_lookup: Callable[[list[str]], dict[str, str]] = lambda codes: {}


def load_config() -> dict:
    if not CONFIG_FILE.exists():
        raise HTTPException(status_code=500, detail=f"{CONFIG_FILE} not found")
    return json.loads(CONFIG_FILE.read_text())


def _rison_str(value: str) -> str:
    # Rison strings are single-quoted; ' and ! are escaped with !
    return "'" + value.replace("!", "!!").replace("'", "!'") + "'"


def kibana_url(base: str, source: dict, query: str, hours: int = WINDOW_HOURS) -> str:
    """Kibana Discover URL for `query` over the last `hours` hours on one source."""
    data_view = source.get("data_view_id") or source["index_pattern"]
    g = f"(time:(from:now-{hours}h,to:now))"
    a = (
        f"(index:{_rison_str(data_view)},"
        f"query:(language:kuery,query:{_rison_str(query)}),"
        f"sort:!(!('@timestamp',desc)))"
    )
    safe = "():,!'*-"
    return f"{base.rstrip('/')}/app/discover#/?_g={quote(g, safe=safe)}&_a={quote(a, safe=safe)}"


def grep_command(log_path: str, hours: int = WINDOW_HOURS) -> str:
    """Shell one-liner to pull the last N hours of FATAL lines from a log glob."""
    return (
        f"find {os.path.dirname(log_path) or '.'} -name '{os.path.basename(log_path)}' "
        f"-mmin -{hours * 60} -exec grep -H 'FATAL' {{}} +"
    )


def parse_line(line: str):
    """Return (timestamp, code) for a FATAL line, or None."""
    if not FATAL_RE.search(line):
        return None
    m = TS_RE.match(line.strip())
    if not m:
        return None
    try:
        ts = datetime.fromisoformat(f"{m.group(1)} {m.group(2)}")
    except ValueError:
        return None
    after_level = line[FATAL_RE.search(line).end():]
    code = CODE_RE.search(after_level)
    return ts, code.group(1) if code else "UNCODED"


def scan_fatals(sources: list[dict], now: datetime, hours: int = WINDOW_HOURS):
    """Scan each source's log files and collect FATAL events inside the window."""
    since = now - timedelta(hours=hours)
    events, files_scanned, missing = [], {}, []
    for src in sources:
        paths = sorted(glob.glob(src["log_path"]))
        files_scanned[src["name"]] = len(paths)
        if not paths:
            missing.append(src["name"])
        for path in paths:
            try:
                if datetime.fromtimestamp(os.path.getmtime(path)) < since:
                    continue
                with open(path, errors="replace") as fh:
                    for line in fh:
                        parsed = parse_line(line)
                        if parsed and since <= parsed[0] <= now:
                            events.append({
                                "source": src["name"],
                                "code": parsed[1],
                                "ts": parsed[0],
                                "file": path,
                                "line": line.strip()[:500],
                            })
            except OSError:
                continue
    return events, files_scanned, missing


def pivot(events: list[dict], sources: list[str], now: datetime, hours: int = WINDOW_HOURS) -> dict:
    """Pivot events: code x source counts, and code x hour-bucket counts."""
    by_code_source = defaultdict(Counter)
    by_code_hour = defaultdict(Counter)
    # Hourly buckets from the hour containing (now - window) through the current hour
    start = (now - timedelta(hours=hours)).replace(minute=0, second=0, microsecond=0)
    buckets = [(start + timedelta(hours=i)).strftime("%m-%d %H:00") for i in range(hours + 1)]
    for e in events:
        by_code_source[e["code"]][e["source"]] += 1
        bucket = e["ts"].replace(minute=0, second=0, microsecond=0).strftime("%m-%d %H:00")
        by_code_hour[e["code"]][bucket] += 1

    codes = sorted(by_code_source, key=lambda c: -sum(by_code_source[c].values()))
    hints = hint_lookup([c for c in codes if c != "UNCODED"])
    rows = []
    for c in codes:
        rows.append({
            "code": c,
            "hint": hints.get(c, ""),
            "total": sum(by_code_source[c].values()),
            "by_source": {s: by_code_source[c][s] for s in sources},
            "by_hour": [by_code_hour[c][b] for b in buckets],
        })
    return {
        "sources": sources,
        "hours": buckets,
        "rows": rows,
        "source_totals": {s: sum(by_code_source[c][s] for c in codes) for s in sources},
        "hour_totals": [sum(by_code_hour[c][b] for c in codes) for b in buckets],
        "total": len(events),
    }


@router.get("/fatal/links")
def fatal_links():
    """Kibana Discover links and log paths for FATAL errors in the last 24h."""
    cfg = load_config()
    base, query = cfg["kibana_base_url"], cfg.get("fatal_query", 'log.level:"FATAL"')
    out = []
    for src in cfg["sources"]:
        field = src.get("service_field")
        q = f'{query} and {field}:"{src["name"]}"' if field else query
        out.append({
            "source": src["name"],
            "kibana_url": kibana_url(base, src, q),
            "kibana_query": q,
            "log_path": src["log_path"],
            "grep": grep_command(src["log_path"]),
        })
    return {"window": f"last {WINDOW_HOURS}h", "sources": out}


@router.get("/fatal/pivot")
def fatal_pivot(top: int = 20):
    """Scan configured log paths and pivot FATAL errors of the last 24h."""
    cfg = load_config()
    now = datetime.now()
    names = [s["name"] for s in cfg["sources"]]
    events, files_scanned, missing = scan_fatals(cfg["sources"], now)
    result = pivot(events, names, now)
    result["rows"] = result["rows"][:top]
    result["generated_at"] = now.isoformat(timespec="seconds")
    result["files_scanned"] = files_scanned
    result["sources_without_files"] = missing
    result["latest"] = [
        {**e, "ts": e["ts"].isoformat(timespec="seconds")}
        for e in sorted(events, key=lambda e: e["ts"], reverse=True)[:25]
    ]
    return result


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard():
    return HTMLResponse((Path(__file__).parent / "static" / "fatal_dashboard.html").read_text())
