#!/usr/bin/env python3
"""
SNMP extend script for cAdvisor metrics compatible with LibreNMS Docker application.
Outputs data in a format that LibreNMS Docker application can parse.
"""
import sys
import time
import argparse
import os
import json
import datetime

try:
    import requests
except ImportError:
    print("ERROR: 'requests' module is not installed.", file=sys.stderr)
    print("Install it with: pip3 install requests", file=sys.stderr)
    sys.exit(1)

VALID_STATES = frozenset(["running", "exited", "paused", "restarting", "created", "removing", "dead"])


def parse_timestamp(ts: str) -> float | None:
    """Parse RFC3339 timestamp to Unix timestamp.

    Handles cAdvisor's nanosecond precision timestamps like:
    '2025-12-05T09:26:39.031046195Z'
    """
    if not ts:
        return None
    try:
        ts_iso = ts.replace("Z", "+00:00")
        if "." in ts_iso and "+" in ts_iso:
            parts = ts_iso.split(".")
            if len(parts) == 2:
                decimal_part = parts[1].split("+")[0]
                if len(decimal_part) > 6:
                    decimal_part = decimal_part[:6]
                ts_iso = parts[0] + "." + decimal_part + "+" + parts[1].split("+")[1]
        return datetime.datetime.fromisoformat(ts_iso).timestamp()
    except ValueError:
        try:
            ts_clean = ts.split(".")[0].rstrip("Z")
            dt = datetime.datetime.strptime(ts_clean, "%Y-%m-%dT%H:%M:%S")
            if ts.endswith("Z"):
                dt = dt.replace(tzinfo=datetime.timezone.utc)
            return dt.timestamp()
        except Exception:
            return None

def fetch_containers(cadvisor_url: str, timeout: float = 2.0) -> dict:
    """Fetch container data from cAdvisor API."""
    url = cadvisor_url.rstrip("/") + "/api/v1.3/docker"
    r = requests.get(url, timeout=timeout)
    r.raise_for_status()
    return r.json()

def calc_cpu_percent(c: dict) -> float:
    """Calculate CPU usage percentage from cumulative counter.

    cAdvisor reports CPU usage as a cumulative counter (like Prometheus).
    We calculate the rate of change over a time window to get CPU percentage.
    Uses the last 2-5 stats points (if available) for stability.
    """
    stats = c.get("stats", [])
    if len(stats) < 2:
        return 0.0

    valid_points = []
    for stat in reversed(stats[-5:]):
        if not isinstance(stat, dict):
            continue
        cpu = stat.get("cpu", {})
        if not isinstance(cpu, dict):
            continue
        usage = cpu.get("usage", {})
        if not isinstance(usage, dict):
            continue
        cu_total = usage.get("total", 0)
        ts = stat.get("timestamp")
        if cu_total is None or not ts:
            continue
        ts_parsed = parse_timestamp(ts)
        if ts_parsed is None:
            continue
        valid_points.append({"timestamp": ts_parsed, "cpu_total_ns": cu_total})

    if len(valid_points) < 2:
        return 0.0

    best_window = None
    best_dt = 0.0
    for i in range(len(valid_points)):
        for j in range(i + 1, len(valid_points)):
            point_a = valid_points[j]
            point_b = valid_points[i]
            dt = point_b["timestamp"] - point_a["timestamp"]
            if dt >= 1.0 and dt > best_dt:
                best_dt = dt
                best_window = (point_a, point_b)
            elif best_window is None and dt >= 0.1 and dt > best_dt:
                best_dt = dt
                best_window = (point_a, point_b)

    if best_window:
        point_a, point_b = best_window
        dt = best_dt
    else:
        point_a = valid_points[-1]
        point_b = valid_points[0]
        dt = max(0.1, point_b["timestamp"] - point_a["timestamp"])

    delta_ns = point_b["cpu_total_ns"] - point_a["cpu_total_ns"]

    if delta_ns < 0 and abs(delta_ns) > point_a["cpu_total_ns"] * 0.5:
        return 0.0
    delta_ns = max(0, delta_ns)

    cpu_rate = (delta_ns / 1e9) / dt

    cpu_limit = c.get("spec", {}).get("cpu", {}).get("limit", 0)
    cpu_count = c.get("spec", {}).get("cpu", {}).get("count", 1)
    if cpu_limit and cpu_limit >= 1e9:
        cpus = cpu_limit / 1e9
    elif cpu_count and cpu_count > 0:
        cpus = cpu_count
    else:
        cpus = 1
    cpus = max(1, cpus)

    return round(min(100.0, cpu_rate * 100.0 / cpus), 2)

def get_state(c: dict) -> str:
    """Get container state: running or stopped.

    cAdvisor only tracks running containers, so if stats exist and are recent
    (< 5 minutes old), the container is running.
    """
    stats = c.get("stats", [])
    if not stats:
        return "stopped"

    ts = stats[-1].get("timestamp") if isinstance(stats[-1], dict) else None
    if ts:
        t_last = parse_timestamp(ts)
        if t_last and (time.time() - t_last) > 300:
            return "stopped"
    return "running"

def get_mem(c: dict) -> tuple[int, int]:
    """Get memory usage and limit in bytes."""
    stats = c.get("stats", [])
    if not stats:
        return 0, 0
    latest_stat = stats[-1]
    if not isinstance(latest_stat, dict):
        return 0, 0

    m = latest_stat.get("memory", {})
    if isinstance(m, dict):
        usage = int(m.get("usage", 0))
    elif isinstance(m, (int, float)):
        usage = int(m)
    else:
        usage = 0

    spec = c.get("spec", {})
    if isinstance(spec, dict):
        memory_spec = spec.get("memory", {})
        if isinstance(memory_spec, dict):
            limit = int(memory_spec.get("limit", 0))
        else:
            limit = 0
    else:
        limit = 0
    return usage, limit

def get_name(c_id: str, c: dict) -> str:
    """Extract container name from various sources."""
    aliases = c.get("aliases") or []
    if aliases:
        return aliases[0].lstrip("/")
    name = c.get("spec", {}).get("labels", {}).get("io.kubernetes.container.name")
    if name:
        return name
    return c.get("name", c_id)[:12].lstrip("/")

def get_pids(c: dict) -> int:
    """Get process count (PIDs) from container stats."""
    stats = c.get("stats", [])
    if not stats:
        return 0
    latest_stat = stats[-1]
    if not isinstance(latest_stat, dict):
        return 0

    processes = latest_stat.get("processes", {})
    if isinstance(processes, dict):
        pids = processes.get("process_count", 0)
        if pids:
            return int(pids)
    elif isinstance(processes, (int, float)):
        return int(processes)

    cpu = latest_stat.get("cpu", {})
    if isinstance(cpu, dict) and "processes" in cpu:
        cpu_processes = cpu.get("processes", [])
        if isinstance(cpu_processes, list):
            return len(cpu_processes)
        elif isinstance(cpu_processes, (int, float)):
            return int(cpu_processes)
    return 0

def get_uptime(c: dict) -> int | None:
    """Calculate container uptime in seconds from creation_time."""
    creation_time = c.get("spec", {}).get("creation_time")
    if not creation_time:
        return None
    creation_ts = parse_timestamp(creation_time)
    if creation_ts:
        return max(0, int(time.time() - creation_ts))
    return None

def get_filesystem_sizes(c: dict) -> tuple[int | None, int | None]:
    """Get filesystem size metrics: size_rw and size_root_fs.

    Note: cAdvisor doesn't expose Docker's size_rw (writable layer size).
    Returns (None, size_root_fs) where size_root_fs is from filesystem stats.
    """
    stats = c.get("stats", [])
    if not stats:
        return None, None

    latest_stat = stats[-1]
    if not isinstance(latest_stat, dict):
        return None, None

    filesystem = latest_stat.get("filesystem", [])
    if filesystem and isinstance(filesystem, list):
        for fs in filesystem:
            if not isinstance(fs, dict):
                continue
            device = fs.get("device", "")
            if device == "/" or "root" in device.lower():
                capacity = fs.get("capacity")
                if isinstance(capacity, dict):
                    size_root_fs = capacity.get("total", 0)
                elif isinstance(capacity, (int, float)):
                    size_root_fs = capacity
                else:
                    size_root_fs = 0
                if size_root_fs:
                    return None, int(size_root_fs)

    spec = c.get("spec", {})
    if isinstance(spec, dict):
        filesystem_spec = spec.get("filesystem", {})
        if isinstance(filesystem_spec, dict):
            size_rw = filesystem_spec.get("size_rw") or filesystem_spec.get("sizeRw")
            size_root_fs = filesystem_spec.get("size_root_fs") or filesystem_spec.get("sizeRootFs")
            if size_rw or size_root_fs:
                return (int(size_rw) if size_rw else None,
                        int(size_root_fs) if size_root_fs else None)

    for stat in reversed(stats):
        if not isinstance(stat, dict):
            continue
        fs_info = stat.get("filesystem", [])
        if fs_info and isinstance(fs_info, list):
            for fs in fs_info:
                if not isinstance(fs, dict):
                    continue
                usage = fs.get("usage")
                if isinstance(usage, dict):
                    total = usage.get("total", 0)
                    if total:
                        return None, int(total)
                elif isinstance(usage, (int, float)) and usage:
                    return None, int(usage)
    return None, None

def format_memory_string(bytes_value: int) -> str:
    """Format memory bytes as a string for LibreNMS Number::toBytes()."""
    if bytes_value == 0:
        return "0B"
    if bytes_value >= 1024 ** 3:
        return f"{bytes_value / (1024 ** 3):.2f}GiB"
    elif bytes_value >= 1024 ** 2:
        return f"{bytes_value / (1024 ** 2):.2f}MiB"
    elif bytes_value >= 1024:
        return f"{bytes_value / 1024:.2f}KiB"
    return f"{bytes_value}B"

def normalize_state(state: str | int | float) -> str:
    """Normalize container state to valid Docker status."""
    if isinstance(state, str):
        status = state.lower().strip()
        if status == "stopped":
            return "exited"
        if status in VALID_STATES:
            return status
        return "running" if status in ("up", "active") else "exited"
    elif isinstance(state, (int, float)):
        return "running" if state == 1 else "exited"
    return "running"

def main():
    ap = argparse.ArgumentParser(description="SNMP extend script for cAdvisor metrics")
    default_url = os.environ.get("CADVISOR_URL", "http://127.0.0.1:8080")
    ap.add_argument("--url", default=default_url,
                    help="cAdvisor base URL (or set CADVISOR_URL env var)")
    args = ap.parse_args()

    try:
        data = fetch_containers(args.url)
    except Exception as e:
        print(f"ERROR: Failed to fetch from cAdvisor: {e}", file=sys.stderr)
        sys.exit(1)

    output = []
    for cid, c in sorted(data.items()):
        try:
            state = get_state(c)
            mem_usage, mem_limit = get_mem(c)
            uptime = get_uptime(c)
            size_rw, size_root_fs = get_filesystem_sizes(c)

            mem_limit_valid = mem_limit if mem_limit and 0 < mem_limit < 2**63 else None
            mem_perc = (mem_usage / mem_limit_valid * 100.0) if mem_limit_valid else 0.0

            output.append({
                "container": get_name(cid, c),
                "cpu": calc_cpu_percent(c),
                "pids": get_pids(c),
                "memory": {
                    "perc": round(mem_perc, 2),
                    "used": format_memory_string(mem_usage),
                    "limit": format_memory_string(mem_limit_valid or 0),
                },
                "state": {
                    "status": normalize_state(state),
                    "uptime": int(uptime) if uptime is not None else None,
                },
                "size": {
                    "size_rw": int(size_rw) if size_rw is not None else None,
                    "size_root_fs": int(size_root_fs) if size_root_fs is not None else None,
                },
            })
        except Exception as e:
            print(f"ERROR: Failed to process container {cid}: {e}", file=sys.stderr)
            continue

    print(json.dumps(output))

if __name__ == "__main__":
    main()