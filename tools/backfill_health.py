#!/usr/bin/env python3
"""Backfill historical Apple Health data from Health Auto Export into InfluxDB.

Connects directly to the Health Auto Export iOS app's TCP/MCP server,
queries health metrics day by day (newest first), and writes to InfluxDB.

Progress is tracked in a JSON file so the import can be interrupted and
resumed without re-importing completed days.

In daemon mode (--daemon), the script keeps trying to complete the
historical backfill. When the phone is unreachable it sleeps and retries.
Once all days in the date range are imported, it exits.

Usage:
    python3 tools/backfill_health.py --hae-host 192.168.1.42
    python3 tools/backfill_health.py --hae-host 192.168.1.42 --daemon
    python3 tools/backfill_health.py --hae-host 192.168.1.42 --daemon --poll-interval 300
    python3 tools/backfill_health.py --hae-host 192.168.1.42 --dry-run -v
    python3 tools/backfill_health.py --hae-host 192.168.1.42 --start 2025-01-01
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import socket
import sys
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS

log = logging.getLogger("backfill")

# ---------------------------------------------------------------------------
# Shared logic (mirrored from custom_components/healthrip/__init__.py)
# ---------------------------------------------------------------------------

SKIP_FIELDS = {"date", "source", "startDate"}
STRING_FIELDS = {"inBedStart", "inBedEnd", "sleepStart", "sleepEnd", "value", "endDate", "start", "end", "context"}


def parse_timestamp(date_str: str) -> datetime:
    """Parse Health Auto Export date strings like '2026-01-19 00:00:00 -0500'."""
    try:
        return datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S %z")
    except ValueError:
        return datetime.fromisoformat(date_str)


def build_points(metrics: list[dict]) -> list[Point]:
    """Convert Health Auto Export metrics into InfluxDB points."""
    points = []
    for metric in metrics:
        name = metric.get("name", "unknown")
        units = metric.get("units", "")

        for dp in metric.get("data", []):
            date_str = dp.get("date") or dp.get("startDate")
            if not date_str:
                continue

            try:
                ts = parse_timestamp(date_str)
            except (ValueError, TypeError):
                log.warning("Skipping data point with unparseable date: %s", date_str)
                continue

            point = Point(name).time(ts, WritePrecision.S)

            source = dp.get("source")
            if source:
                point = point.tag("source", source)
            if units:
                point = point.tag("units", units)

            field_count = 0
            for key, value in dp.items():
                if key in SKIP_FIELDS:
                    continue
                if key in STRING_FIELDS:
                    if isinstance(value, str):
                        point = point.tag(key, value)
                    continue
                if isinstance(value, (int, float)):
                    point = point.field(key.lower(), float(value))
                    field_count += 1

            if field_count > 0:
                points.append(point)

    return points


# ---------------------------------------------------------------------------
# TCP client for Health Auto Export MCP server
# ---------------------------------------------------------------------------


def query_health_metrics(
    host: str,
    port: int,
    start: str,
    end: str,
    metrics: str = "",
    timeout: int = 60,
) -> dict:
    """Send a health_metrics query to Health Auto Export TCP server."""
    request = {
        "jsonrpc": "2.0",
        "id": str(uuid.uuid4()),
        "method": "callTool",
        "params": {
            "name": "health_metrics",
            "arguments": {
                "start": start,
                "end": end,
                "metrics": metrics,
                "interval": "",
                "aggregate": False,
            },
        },
    }

    payload = json.dumps(request).encode("utf-8")
    log.debug("TCP request to %s:%d: %s", host, port, json.dumps(request, indent=2))

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect((host, port))
        sock.sendall(payload)
        sock.shutdown(socket.SHUT_WR)

        chunks = []
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)

        raw = b"".join(chunks).decode("utf-8")
        log.debug("TCP response (%d bytes): %.500s", len(raw), raw)
        return json.loads(raw)
    finally:
        sock.close()


def query_with_retry(
    host: str,
    port: int,
    start: str,
    end: str,
    metrics: str = "",
    retries: int = 3,
    delay: float = 5.0,
    timeout: int = 60,
) -> dict:
    """Query with retry on connection errors."""
    for attempt in range(retries):
        try:
            return query_health_metrics(host, port, start, end, metrics, timeout)
        except (ConnectionRefusedError, socket.timeout, OSError) as e:
            if attempt < retries - 1:
                log.warning(
                    "Attempt %d/%d failed: %s. Retrying in %.0fs...",
                    attempt + 1,
                    retries,
                    e,
                    delay,
                )
                time.sleep(delay)
            else:
                raise


def extract_metrics(response: dict) -> list[dict]:
    """Extract metrics list from JSON-RPC response.

    Handles both the MCP wrapper format (content[].text with embedded JSON)
    and a direct format where data is at the top level of the result.
    """
    if "error" in response:
        raise RuntimeError(f"JSON-RPC error: {response['error']}")

    result = response.get("result", {})

    # MCP tool response: result.content[0].text contains JSON string
    content = result.get("content", [])
    if content and isinstance(content, list):
        for item in content:
            if item.get("type") == "text":
                try:
                    inner = json.loads(item["text"])
                    metrics = inner.get("data", {}).get("metrics", [])
                    if metrics:
                        return metrics
                except (json.JSONDecodeError, AttributeError):
                    pass

    # Direct format: result.data.metrics
    if "data" in result:
        metrics = result["data"].get("metrics", [])
        if metrics:
            return metrics

    # Top-level data (if response itself is the payload)
    if "data" in response:
        metrics = response["data"].get("metrics", [])
        if metrics:
            return metrics

    return []


# ---------------------------------------------------------------------------
# Progress tracking
# ---------------------------------------------------------------------------


class ProgressTracker:
    """Track which days have been imported, persisted to a JSON file."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.completed: set[str] = set()
        self.points_by_day: dict[str, int] = {}
        self.total_points: int = 0
        self._dirty = False

    def load(self) -> None:
        if self.path.exists():
            data = json.loads(self.path.read_text())
            self.completed = set(data.get("completed_days", []))
            self.points_by_day = data.get("points_by_day", {})
            self.total_points = data.get("total_points", 0)
            log.info(
                "Loaded progress: %d days already completed, %d total points",
                len(self.completed),
                self.total_points,
            )
        else:
            log.info("No progress file found, starting fresh")

    def save(self) -> None:
        data = {
            "completed_days": sorted(self.completed),
            "last_updated": datetime.now(timezone.utc).isoformat(),
            "total_completed": len(self.completed),
            "total_points": self.total_points,
            "points_by_day": dict(sorted(self.points_by_day.items())),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=2) + "\n")
        self._dirty = False
        log.debug("Progress saved (%d days, %d points)", len(self.completed), self.total_points)

    def mark_completed(self, day: date, points: int = 0) -> None:
        self.completed.add(day.isoformat())
        if points > 0:
            self.points_by_day[day.isoformat()] = points
            self.total_points += points
        self._dirty = True

    def is_completed(self, day: date) -> bool:
        return day.isoformat() in self.completed

    def reset(self) -> None:
        if self.path.exists():
            self.path.unlink()
            log.info("Progress file deleted")
        self.completed.clear()

    def save_if_dirty(self) -> None:
        if self._dirty:
            self.save()


# ---------------------------------------------------------------------------
# InfluxDB writer
# ---------------------------------------------------------------------------


def write_points(
    points: list[Point],
    url: str,
    token: str,
    org: str,
    bucket: str,
) -> None:
    """Write a batch of points to InfluxDB."""
    client = InfluxDBClient(url=url, token=token, org=org)
    try:
        write_api = client.write_api(write_options=SYNCHRONOUS)
        write_api.write(bucket=bucket, record=points)
    finally:
        client.close()


def write_telemetry(
    args,
    tracker: ProgressTracker,
    day: date,
    day_points: int,
    query_dur: float,
    write_dur: float,
    total_dur: float,
    total_days: int,
) -> None:
    """Write import telemetry to InfluxDB."""
    if args.dry_run:
        return
    now = datetime.now(timezone.utc)
    remaining = total_days - len(tracker.completed)
    pct = len(tracker.completed) / total_days * 100 if total_days else 0
    telemetry_points = [
        Point("backfill_day")
        .tag("date", day.isoformat())
        .field("points", float(day_points))
        .field("query_duration_s", round(query_dur, 3))
        .field("write_duration_s", round(write_dur, 3))
        .field("total_duration_s", round(total_dur, 3))
        .time(now, WritePrecision.S),
        Point("backfill_progress")
        .field("completed", float(len(tracker.completed)))
        .field("remaining", float(remaining))
        .field("pct_complete", round(pct, 2))
        .field("total_points", float(tracker.total_points))
        .time(now, WritePrecision.S),
    ]
    try:
        write_points(
            telemetry_points,
            args.influx_url,
            args.influx_token,
            args.influx_org,
            args.influx_bucket,
        )
    except Exception as e:
        log.warning("Failed to write telemetry: %s", e)


def write_connectivity(args, online: bool) -> None:
    """Write a connectivity state change to InfluxDB."""
    if args.dry_run:
        return
    now = datetime.now(timezone.utc)
    point = (
        Point("backfill_connectivity")
        .field("online", 1.0 if online else 0.0)
        .time(now, WritePrecision.S)
    )
    try:
        write_points([point], args.influx_url, args.influx_token, args.influx_org, args.influx_bucket)
    except Exception as e:
        log.warning("Failed to write connectivity telemetry: %s", e)


def write_error(args, day: date, error: str) -> None:
    """Write an import error event to InfluxDB."""
    if args.dry_run:
        return
    now = datetime.now(timezone.utc)
    point = (
        Point("backfill_error")
        .tag("date", day.isoformat())
        .tag("error_type", error.split(":")[0].strip() if error else "unknown")
        .field("message", error[:200])
        .field("count", 1.0)
        .time(now, WritePrecision.S)
    )
    try:
        write_points([point], args.influx_url, args.influx_token, args.influx_org, args.influx_bucket)
    except Exception as e:
        log.warning("Failed to write error telemetry: %s", e)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def date_range_reverse(start_date: date, end_date: date):
    """Yield each date from end to start inclusive (newest first)."""
    current = end_date
    while current >= start_date:
        yield current
        current -= timedelta(days=1)


def format_hae_timestamp(d: date, h: int, m: int, s: int, tz: str) -> str:
    """Format a date+time into HAE timestamp format."""
    return f"{d.isoformat()} {h:02d}:{m:02d}:{s:02d} {tz}"


def get_local_tz_offset() -> str:
    """Get local timezone offset as ±HHMM string."""
    offset = datetime.now(timezone.utc).astimezone().utcoffset()
    if offset is None:
        return "+0000"
    total_seconds = int(offset.total_seconds())
    sign = "+" if total_seconds >= 0 else "-"
    total_seconds = abs(total_seconds)
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    return f"{sign}{hours:02d}{minutes:02d}"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Backfill historical health data from Health Auto Export to InfluxDB",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
examples:
  %(prog)s --hae-host 192.168.1.42
  %(prog)s --hae-host 192.168.1.42 --daemon
  %(prog)s --hae-host 192.168.1.42 --daemon --poll-interval 120
  %(prog)s --hae-host 192.168.1.42 --dry-run -v
  %(prog)s --hae-host 192.168.1.42 --start 2025-01-01 --end 2025-12-31
  %(prog)s --hae-host 192.168.1.42 --reset
""",
    )

    p.add_argument(
        "--hae-host",
        default=os.environ.get("HAE_HOST"),
        help="Health Auto Export device IP (env: HAE_HOST)",
    )
    p.add_argument(
        "--hae-port",
        type=int,
        default=int(os.environ.get("HAE_PORT", "9000")),
        help="HAE TCP port (default: 9000, env: HAE_PORT)",
    )
    p.add_argument(
        "--start",
        type=date.fromisoformat,
        default=date(2015, 1, 1),
        help="Start date YYYY-MM-DD (default: 2015-01-01)",
    )
    p.add_argument(
        "--end",
        type=date.fromisoformat,
        default=date.today(),
        help="End date YYYY-MM-DD (default: today)",
    )
    p.add_argument(
        "--influx-url",
        default=os.environ.get("INFLUXDB_URL"),
        help="InfluxDB URL (env: INFLUXDB_URL)",
    )
    p.add_argument(
        "--influx-token",
        default=os.environ.get("INFLUXDB_TOKEN"),
        help="InfluxDB API token (env: INFLUXDB_TOKEN)",
    )
    p.add_argument(
        "--influx-org",
        default=os.environ.get("INFLUXDB_ORG", "homeassistant"),
        help="InfluxDB org (default: homeassistant, env: INFLUXDB_ORG)",
    )
    p.add_argument(
        "--influx-bucket",
        default=os.environ.get("INFLUXDB_BUCKET", "health"),
        help="InfluxDB bucket (default: health, env: INFLUXDB_BUCKET)",
    )
    p.add_argument(
        "--tz-offset",
        default=None,
        help="Timezone offset for queries, e.g. -0500 (default: auto-detect)",
    )
    p.add_argument(
        "--metrics",
        default="",
        help="Comma-separated metric names to import (default: all)",
    )
    p.add_argument(
        "--progress-file",
        default=str(Path(__file__).parent / "import_progress.json"),
        help="Path to progress tracking file",
    )
    p.add_argument("--reset", action="store_true", help="Clear progress and start over")
    p.add_argument(
        "--dry-run", action="store_true", help="Query data but don't write to InfluxDB"
    )
    p.add_argument(
        "--delay",
        type=float,
        default=0.5,
        help="Seconds between requests (default: 0.5)",
    )
    p.add_argument("-v", "--verbose", action="store_true", help="Verbose logging")
    p.add_argument(
        "--daemon",
        action="store_true",
        help="Run continuously, polling for the phone and importing when reachable",
    )
    p.add_argument(
        "--poll-interval",
        type=int,
        default=int(os.environ.get("POLL_INTERVAL", "30")),
        help="Seconds between polls when phone is unreachable or all caught up (default: 30)",
    )

    args = p.parse_args()

    if not args.hae_host:
        p.error("--hae-host is required (or set HAE_HOST env var)")
    if not args.dry_run:
        if not args.influx_url:
            p.error("--influx-url is required (or set INFLUXDB_URL env var)")
        if not args.influx_token:
            p.error("--influx-token is required (or set INFLUXDB_TOKEN env var)")

    if args.tz_offset is None:
        args.tz_offset = get_local_tz_offset()

    return args


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

_interrupted = False


def check_phone_reachable(host: str, port: int, timeout: int = 5) -> bool:
    """Quick check whether the HAE server is accepting connections."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect((host, port))
        sock.close()
        return True
    except (ConnectionRefusedError, socket.timeout, OSError):
        return False


def import_pass(args, tracker: ProgressTracker) -> tuple[int, int, int, bool]:
    """Run one import pass over remaining days.

    Returns (days_imported, total_points, days_failed, phone_lost).
    phone_lost is True if the pass ended due to network failures (phone went away).
    """
    global _interrupted

    all_days = list(date_range_reverse(args.start, args.end))
    remaining = [d for d in all_days if not tracker.is_completed(d)]

    total = len(all_days)
    done = total - len(remaining)
    log.info(
        "Date range: %s to %s (%d days total, %d completed, %d remaining)",
        args.start,
        args.end,
        total,
        done,
        len(remaining),
    )

    if not remaining:
        log.info("All days already imported!")
        return 0, 0, 0, False

    if args.dry_run:
        log.info("DRY RUN — will query but not write to InfluxDB")

    total_days = len(all_days)
    total_points = 0
    days_imported = 0
    consecutive_net_failures = 0
    phone_lost = False

    try:
        for i, day in enumerate(remaining):
            if _interrupted:
                break

            # Split each day into 6-hour windows to reduce per-request payload
            windows = [
                (0, 0, 0, 5, 59, 59),
                (6, 0, 0, 11, 59, 59),
                (12, 0, 0, 17, 59, 59),
                (18, 0, 0, 23, 59, 59),
            ]

            try:
                day_points = 0
                query_total = 0.0
                write_total = 0.0
                day_start = time.monotonic()

                for sh, sm, ss, eh, em, es in windows:
                    if _interrupted:
                        break

                    start_ts = format_hae_timestamp(day, sh, sm, ss, args.tz_offset)
                    end_ts = format_hae_timestamp(day, eh, em, es, args.tz_offset)

                    t0 = time.monotonic()
                    response = query_with_retry(
                        args.hae_host,
                        args.hae_port,
                        start_ts,
                        end_ts,
                        metrics=args.metrics,
                    )
                    query_total += time.monotonic() - t0

                    metrics = extract_metrics(response)
                    points = build_points(metrics)

                    if points and not args.dry_run:
                        t0 = time.monotonic()
                        write_points(
                            points,
                            args.influx_url,
                            args.influx_token,
                            args.influx_org,
                            args.influx_bucket,
                        )
                        write_total += time.monotonic() - t0

                    day_points += len(points)
                    consecutive_net_failures = 0

                    log.info(
                        "%s  %02d:00-%02d:59  %6d pts",
                        day, sh, eh, len(points),
                    )

                day_elapsed = time.monotonic() - day_start

                tracker.mark_completed(day, day_points)
                days_imported += 1
                total_points += day_points

                write_telemetry(
                    args, tracker, day, day_points,
                    query_total, write_total, day_elapsed, total_days,
                )

                log.info(
                    "%s  TOTAL %d points  (%d/%d remaining)  [query: %.1fs  write: %.1fs  total: %.1fs]",
                    day,
                    day_points,
                    len(remaining) - i - 1,
                    len(remaining),
                    query_total,
                    write_total,
                    day_elapsed,
                )

            except (ConnectionRefusedError, socket.timeout, OSError) as e:
                consecutive_net_failures += 1
                log.warning(
                    "%s  FAILED (network): %s",
                    day,
                    e,
                )
                write_error(args, day, str(e))
                if consecutive_net_failures >= 3:
                    log.warning("Phone unreachable after %d consecutive failures.", consecutive_net_failures)
                    phone_lost = True
                    write_connectivity(args, online=False)
                    break
            except Exception as e:
                log.error("%s  FAILED: %s", day, e)
                write_error(args, day, str(e))

            # Save progress after every day
            tracker.save_if_dirty()

            if args.delay > 0 and not _interrupted:
                time.sleep(args.delay)

    finally:
        tracker.save_if_dirty()

    log.info(
        "Pass complete. Imported %d days (%d points).",
        days_imported,
        total_points,
    )

    return days_imported, total_points, consecutive_net_failures, phone_lost


def main() -> None:
    global _interrupted

    # Load .env from script directory
    load_dotenv(Path(__file__).parent / ".env")
    # Also try repo root .env
    load_dotenv(Path(__file__).parent.parent / ".env")

    args = parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-5s %(message)s",
        datefmt="%H:%M:%S",
    )

    def handle_interrupt(sig, frame):
        global _interrupted
        _interrupted = True
        log.info("\nInterrupt received, finishing current day...")

    signal.signal(signal.SIGINT, handle_interrupt)

    tracker = ProgressTracker(args.progress_file)
    if args.reset:
        tracker.reset()
    tracker.load()

    if not args.daemon:
        # One-shot mode (original behavior)
        days_imported, total_points, days_failed, _ = import_pass(args, tracker)
        all_days = list(date_range_reverse(args.start, args.end))
        completed_total = len([d for d in all_days if tracker.is_completed(d)])
        total = len(all_days)
        log.info(
            "Done. Imported %d days (%d points). Total progress: %d/%d days (%.0f%%). Failed: %d.",
            days_imported,
            total_points,
            completed_total,
            total,
            completed_total / total * 100 if total else 0,
            days_failed,
        )
        return

    # Daemon mode — keep trying until historical backfill is complete
    log.info("Starting daemon (poll interval: %ds)", args.poll_interval)
    was_online = False

    while not _interrupted:
        if check_phone_reachable(args.hae_host, args.hae_port):
            if not was_online:
                log.info("Phone came online at %s:%d", args.hae_host, args.hae_port)
                write_connectivity(args, online=True)
                was_online = True

            log.info("Phone reachable at %s:%d — starting import pass", args.hae_host, args.hae_port)
            tracker.load()  # reload in case of external edits
            days_imported, total_points, _, phone_lost = import_pass(args, tracker)

            if _interrupted:
                break

            if phone_lost:
                log.info("Phone went away during import. Will retry in %ds.", args.poll_interval)
                was_online = False
            elif days_imported == 0:
                log.info("Historical backfill complete!")
                break
            else:
                log.info("Pass finished. Sleeping %ds before next check.", args.poll_interval)
        else:
            if was_online:
                log.info("Phone went offline.")
                write_connectivity(args, online=False)
                was_online = False
            log.info("Phone not reachable at %s:%d. Sleeping %ds.", args.hae_host, args.hae_port, args.poll_interval)

        # Sleep in small increments so Ctrl+C is responsive
        for _ in range(args.poll_interval):
            if _interrupted:
                break
            time.sleep(1)

    log.info("Daemon stopped.")


if __name__ == "__main__":
    main()
