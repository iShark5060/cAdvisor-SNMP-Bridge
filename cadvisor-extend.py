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

def fetch_containers(cadvisor_url: str, timeout: float = 2.0):
    """Fetch container data from cAdvisor API."""
    url = cadvisor_url.rstrip("/") + "/api/v1.3/docker"
    r = requests.get(url, timeout=timeout)
    r.raise_for_status()
    return r.json()

def calc_cpu_percent(c):
    """Calculate CPU usage percentage.

    Requires at least 2 stats points to calculate delta.
    If only 1 stat point available, returns 0.0.
    """
    stats = c.get("stats", [])
    if len(stats) < 2:
        return 0.0

    a = stats[-2]
    b = stats[-1]
    cu_a = a.get("cpu", {}).get("usage", {}).get("total", 0)
    cu_b = b.get("cpu", {}).get("usage", {}).get("total", 0)
    ts_a = a.get("timestamp")
    ts_b = b.get("timestamp")

    if not cu_a or not cu_b:
        return 0.0

    def parse_timestamp(ts):
        """Parse RFC3339 timestamp to Unix timestamp."""
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

            dt = datetime.datetime.fromisoformat(ts_iso)
            return dt.timestamp()
        except ValueError:
            try:
                ts_clean = ts.split(".")[0].rstrip("Z")
                dt = datetime.datetime.strptime(ts_clean, "%Y-%m-%dT%H:%M:%S")
                if ts.endswith("Z"):
                    dt = dt.replace(tzinfo=datetime.timezone.utc)
                return dt.timestamp()
            except Exception:
                return None

    try:
        ta = parse_timestamp(ts_a)
        tb = parse_timestamp(ts_b)
        if ta is None or tb is None:
            return 0.0
    except Exception:
        return 0.0

    dt = max(0.001, tb - ta)
    delta_ns = max(0, cu_b - cu_a)
    cpu_seconds = delta_ns / 1e9

    cpu_limit = c.get("spec", {}).get("cpu", {}).get("limit", 0)
    cpu_count = c.get("spec", {}).get("cpu", {}).get("count", 1)

    if cpu_limit and cpu_limit >= 1e9:
        cpus = cpu_limit / 1e9
    elif cpu_count and cpu_count > 0:
        cpus = cpu_count
    else:
        cpus = 1

    if cpus <= 0:
        cpus = 1
    pct = min(100.0, (cpu_seconds / dt) * 100.0 / cpus)
    return round(pct, 2)

def get_state(c):
    """Get container state: running or stopped.

    Uses multiple heuristics:
    1. Check if stats exist and are recent (< 5 minutes old)
    2. Default to running if stats exist (cAdvisor only tracks running containers)
    """
    stats = c.get("stats", [])
    if not stats:
        return "stopped"
    ts = stats[-1].get("timestamp")
    if ts:
        try:
            ts_iso = ts.replace("Z", "+00:00")
            if "." in ts_iso and "+" in ts_iso:
                parts = ts_iso.split(".")
                if len(parts) == 2:
                    decimal_part = parts[1].split("+")[0]
                    if len(decimal_part) > 6:
                        decimal_part = decimal_part[:6]
                    ts_iso = parts[0] + "." + decimal_part + "+" + parts[1].split("+")[1]

            try:
                dt = datetime.datetime.fromisoformat(ts_iso)
                t_last = dt.timestamp()
            except ValueError:
                ts_clean = ts.split(".")[0].rstrip("Z")
                dt = datetime.datetime.strptime(ts_clean, "%Y-%m-%dT%H:%M:%S")
                if ts.endswith("Z"):
                    dt = dt.replace(tzinfo=datetime.timezone.utc)
                t_last = dt.timestamp()

            time_diff = time.time() - t_last
            if time_diff > 300:
                return "stopped"
            return "running"
        except Exception as e:
            print(f"WARNING: Timestamp parsing failed for {ts}: {e}", file=sys.stderr)
            return "running"
    return "running"

def get_mem(c):
    """Get memory usage and limit in bytes."""
    stats = c.get("stats", [])
    if not stats:
        return 0, 0
    m = stats[-1].get("memory", {})
    usage = int(m.get("usage", 0))
    limit = int(c.get("spec", {}).get("memory", {}).get("limit", 0))
    return usage, limit

def get_name(c_id: str, c: dict) -> str:
    """Extract container name from various sources."""
    n = c.get("aliases") or []
    if n:
        return n[0].lstrip("/")
    name = c.get("spec", {}).get("labels", {}).get("io.kubernetes.container.name")
    if name:
        return name
    return c.get("name", c_id)[:12].lstrip("/")

def get_pids(c: dict) -> int:
    """Get process count (PIDs) from container stats."""
    stats = c.get("stats", [])
    if not stats:
        return 0
    pids = stats[-1].get("processes", {}).get("process_count", 0)
    if pids:
        return int(pids)
    cpu = stats[-1].get("cpu", {})
    if "processes" in cpu:
        return len(cpu.get("processes", []))
    return 0

def get_uptime(c: dict) -> int:
    """Calculate container uptime in seconds from creation_time.

    Returns uptime in seconds, or None if creation_time is not available.
    """
    creation_time = c.get("spec", {}).get("creation_time")
    if not creation_time:
        return None

    def parse_timestamp(ts):
        """Parse RFC3339 timestamp to Unix timestamp."""
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
            dt = datetime.datetime.fromisoformat(ts_iso)
            return dt.timestamp()
        except ValueError:
            try:
                ts_clean = ts.split(".")[0].rstrip("Z")
                dt = datetime.datetime.strptime(ts_clean, "%Y-%m-%dT%H:%M:%S")
                if ts.endswith("Z"):
                    dt = dt.replace(tzinfo=datetime.timezone.utc)
                return dt.timestamp()
            except Exception:
                return None

    try:
        creation_ts = parse_timestamp(creation_time)
        if creation_ts:
            uptime_seconds = int(time.time() - creation_ts)
            return max(0, uptime_seconds)
    except Exception:
        pass

    return None

def get_filesystem_sizes(c: dict):
    """Get filesystem size metrics: size_rw and size_root_fs.
    
    Returns tuple (size_rw, size_root_fs) in bytes, or (None, None) if not available.
    These metrics come from Docker filesystem stats:
    - size_rw: Size of the read-write layer (container's writable layer)
    - size_root_fs: Size of the root filesystem (base image + all layers)
    """
    stats = c.get("stats", [])
    if not stats:
        return None, None

    latest_stat = stats[-1]

    filesystem = latest_stat.get("filesystem", [])
    if filesystem:
        for fs in filesystem:
            device = fs.get("device", "")
            if device == "/" or "root" in device.lower():
                size_root_fs = fs.get("capacity", {}).get("total", 0)
                if size_root_fs:
                    return None, int(size_root_fs)

    spec = c.get("spec", {})

    filesystem_spec = spec.get("filesystem", {})
    if filesystem_spec:
        size_rw = filesystem_spec.get("size_rw") or filesystem_spec.get("sizeRw")
        size_root_fs = filesystem_spec.get("size_root_fs") or filesystem_spec.get("sizeRootFs")
        if size_rw or size_root_fs:
            return int(size_rw) if size_rw else None, int(size_root_fs) if size_root_fs else None

    for stat in reversed(stats):
        fs_info = stat.get("filesystem", [])
        if fs_info:
            for fs in fs_info:
                usage = fs.get("usage", {})
                if usage:
                    total = usage.get("total", 0)
                    if total:
                        return None, int(total)

    return None, None

def format_memory_string(bytes_value: int) -> str:
    """Format memory bytes as a string that Number::toBytes() can parse.
    
    Formats as: "100MB", "100MiB", "1.5GB", etc.
    LibreNMS Number::toBytes() can parse formats like:
    - "100MB" or "100 MB"
    - "100MiB" or "100 MiB"  
    - "1000000" (bytes as string)
    """
    if bytes_value == 0:
        return "0B"
    if bytes_value >= 1024 * 1024 * 1024:
        gib = bytes_value / (1024 * 1024 * 1024)
        return f"{gib:.2f}GiB"
    elif bytes_value >= 1024 * 1024:
        mib = bytes_value / (1024 * 1024)
        return f"{mib:.2f}MiB"
    elif bytes_value >= 1024:
        kib = bytes_value / 1024
        return f"{kib:.2f}KiB"
    else:
        return f"{bytes_value}B"

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

    containers = []
    for cid, c in sorted(data.items()):
        try:
            name = get_name(cid, c)
            state = get_state(c)
            cpu = calc_cpu_percent(c)
            mem, memlimit = get_mem(c)
            pids = get_pids(c)
            uptime = get_uptime(c)
            size_rw, size_root_fs = get_filesystem_sizes(c)

            containers.append({
                "container": name,
                "state": state,
                "cpu": cpu,
                "memory": mem,
                "memory_limit": memlimit,
                "pids": pids,
                "uptime": uptime,
                "size_rw": size_rw,
                "size_root_fs": size_root_fs,
            })
        except Exception as e:
            print(f"ERROR: Failed to process container {cid}: {e}", file=sys.stderr)
            continue

    librenms_format = []
    for c in containers:
        memory_bytes = int(c["memory"]) if c["memory"] else 0
        memory_limit_bytes = int(c["memory_limit"]) if c["memory_limit"] and c["memory_limit"] > 0 and c["memory_limit"] < 2**63 else None

        memory_perc = 0.0
        if memory_limit_bytes and memory_limit_bytes > 0:
            memory_perc = (memory_bytes / memory_limit_bytes) * 100.0

        memory_used_str = format_memory_string(memory_bytes)
        if memory_limit_bytes:
            memory_limit_str = format_memory_string(memory_limit_bytes)
        else:
            memory_limit_str = "0B"

        if isinstance(c["state"], str):
            state_status = c["state"].lower()
            if state_status == "stopped":
                state_status = "exited"
            elif state_status not in ["running", "exited", "paused", "restarting", "created", "removing", "dead"]:
                state_status = "running" if state_status in ["up", "active"] else "exited"
        else:
            state_status = "running" if c["state"] == 1 else "exited"

        container_data = {
            "container": c["container"],
            "cpu": float(c["cpu"]),
            "pids": int(c["pids"]),
            "memory": {
                "perc": round(memory_perc, 2),
                "used": memory_used_str,
                "limit": memory_limit_str,
            },
            "state": {
                "status": state_status,
                "uptime": c.get("uptime"),
            },
            "size": {
                "size_rw": c.get("size_rw"),
                "size_root_fs": c.get("size_root_fs"),
            },
        }

        librenms_format.append(container_data)
    print(json.dumps(librenms_format))

if __name__ == "__main__":
    main()