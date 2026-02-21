#!/usr/bin/env python3
"""Report health data import progress for the ha-health-ingester backfill daemon.

Reads import_progress.json and systemd service status to produce a concise
progress report suitable for the chat assistant.

Usage:
    python3 import_status.py          # human-readable report
    python3 import_status.py --json   # machine-readable JSON
"""

import json
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path


PROGRESS_FILE = Path(__file__).parent / "import_progress.json"
SERVICE_NAME = "health-backfill"
DEFAULT_START = date(2015, 1, 1)


def load_progress() -> dict:
    if not PROGRESS_FILE.exists():
        return {"completed_days": [], "last_updated": None, "total_completed": 0}
    return json.loads(PROGRESS_FILE.read_text())


def get_service_status() -> dict:
    """Get systemd service status."""
    info = {"active": False, "status": "unknown", "since": None, "pid": None}
    try:
        result = subprocess.run(
            ["systemctl", "--user", "show", SERVICE_NAME,
             "--property=ActiveState,SubState,MainPID,ActiveEnterTimestamp"],
            capture_output=True, text=True, timeout=5
        )
        for line in result.stdout.strip().split("\n"):
            if "=" not in line:
                continue
            key, val = line.split("=", 1)
            if key == "ActiveState":
                info["active"] = val == "active"
                info["status"] = val
            elif key == "SubState":
                info["sub_status"] = val
            elif key == "MainPID" and val != "0":
                info["pid"] = int(val)
            elif key == "ActiveEnterTimestamp" and val:
                info["since"] = val.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return info


def compute_stats(progress: dict) -> dict:
    completed_days = sorted(progress.get("completed_days", []))
    total_completed = len(completed_days)
    last_updated = progress.get("last_updated")

    today = date.today()
    total_days = (today - DEFAULT_START).days + 1
    remaining = total_days - total_completed

    # Date coverage
    earliest = completed_days[0] if completed_days else None
    latest = completed_days[-1] if completed_days else None

    # Gaps (missing days within the completed range)
    gaps = []
    if len(completed_days) >= 2:
        completed_set = set(completed_days)
        range_start = date.fromisoformat(earliest)
        range_end = date.fromisoformat(latest)
        d = range_start
        from datetime import timedelta
        while d <= range_end:
            if d.isoformat() not in completed_set:
                gaps.append(d.isoformat())
            d += timedelta(days=1)

    # Rate calculation — average days imported per calendar day since first import
    rate_per_day = None
    eta_days = None
    if last_updated and total_completed > 1 and earliest:
        first_import = date.fromisoformat(earliest)
        last_update_dt = datetime.fromisoformat(last_updated)
        elapsed_calendar_days = (last_update_dt.date() - first_import).days or 1
        rate_per_day = total_completed / elapsed_calendar_days
        if rate_per_day > 0 and remaining > 0:
            eta_days = int(remaining / rate_per_day)

    # Time since last update
    since_last = None
    if last_updated:
        last_dt = datetime.fromisoformat(last_updated)
        delta = datetime.now(timezone.utc) - last_dt
        hours = delta.total_seconds() / 3600
        if hours < 1:
            since_last = f"{int(delta.total_seconds() / 60)}m ago"
        elif hours < 24:
            since_last = f"{hours:.1f}h ago"
        else:
            since_last = f"{delta.days}d ago"

    pct = (total_completed / total_days * 100) if total_days > 0 else 0

    return {
        "total_days_in_range": total_days,
        "completed": total_completed,
        "remaining": remaining,
        "percent_complete": round(pct, 1),
        "range_start": DEFAULT_START.isoformat(),
        "range_end": today.isoformat(),
        "earliest_imported": earliest,
        "latest_imported": latest,
        "gaps_in_completed_range": gaps,
        "gap_count": len(gaps),
        "last_updated": last_updated,
        "since_last_update": since_last,
        "rate_days_per_day": round(rate_per_day, 1) if rate_per_day else None,
        "estimated_days_to_complete": eta_days,
    }


def format_report(stats: dict, service: dict) -> str:
    lines = []
    lines.append("=== Health Data Import Status ===")
    lines.append("")

    # Service status
    status_str = service.get("status", "unknown")
    sub = service.get("sub_status", "")
    if sub:
        status_str += f" ({sub})"
    if service.get("pid"):
        status_str += f", PID {service['pid']}"
    if service.get("since"):
        status_str += f", since {service['since']}"
    lines.append(f"Service:    {status_str}")
    lines.append("")

    # Progress
    lines.append(f"Progress:   {stats['completed']}/{stats['total_days_in_range']} days ({stats['percent_complete']}%)")
    lines.append(f"Remaining:  {stats['remaining']} days")
    lines.append(f"Date range: {stats['range_start']} to {stats['range_end']}")
    lines.append("")

    # Coverage
    if stats["earliest_imported"] and stats["latest_imported"]:
        lines.append(f"Imported:   {stats['earliest_imported']} through {stats['latest_imported']}")
    if stats["gap_count"] > 0:
        gap_preview = ", ".join(stats["gaps_in_completed_range"][:5])
        if stats["gap_count"] > 5:
            gap_preview += f" ... (+{stats['gap_count'] - 5} more)"
        lines.append(f"Gaps:       {stats['gap_count']} missing day(s): {gap_preview}")
    lines.append("")

    # Rates
    if stats["rate_days_per_day"]:
        lines.append(f"Rate:       ~{stats['rate_days_per_day']} days imported per calendar day")
    if stats["estimated_days_to_complete"]:
        lines.append(f"ETA:        ~{stats['estimated_days_to_complete']} days to complete at current rate")
    lines.append("")

    # Last contact
    if stats["since_last_update"]:
        lines.append(f"Last update: {stats['since_last_update']}")

    return "\n".join(lines)


def main():
    as_json = "--json" in sys.argv

    progress = load_progress()
    stats = compute_stats(progress)
    service = get_service_status()

    if as_json:
        output = {**stats, "service": service}
        print(json.dumps(output, indent=2))
    else:
        print(format_report(stats, service))


if __name__ == "__main__":
    main()
