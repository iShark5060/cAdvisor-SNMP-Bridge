#!/usr/bin/env python3
# Prettier width 80
import sys
import time
import argparse
import hashlib
import requests

BASE_OID = ".1.3.6.1.4.1.424242.2.1"  # table root

# Columns:
# .<idx>.1 name (string)
# .<idx>.2 state (integer: 1 running, 2 stopped)
# .<idx>.3 cpuHundredths (integer)
# .<idx>.4 memBytes (counter64)
# .<idx>.5 memLimitBytes (counter64)
# .<idx>.6 restartCount (counter32)

def stable_index(name: str) -> int:
    # stable small-ish positive index from name
    h = hashlib.sha1(name.encode("utf-8")).digest()
    return int.from_bytes(h[:2], "big") or 1

def fetch_containers(cadvisor_url: str, timeout: float = 3.0):
    # Use /api/v1.3/docker for container list and stats
    url = cadvisor_url.rstrip("/") + "/api/v1.3/docker"
    r = requests.get(url, timeout=timeout)
    r.raise_for_status()
    data = r.json()
    # data is a dict keyed by container ID, each with stats and spec
    return data

def calc_cpu_hundredths(c):
    # Try to compute recent cpu percentage from last two samples
    stats = c.get("stats", [])
    if len(stats) < 2:
        return 0
    a = stats[-2]
    b = stats[-1]
    # total usage nanoseconds across all CPUs
    cu_a = a.get("cpu", {}).get("usage", {}).get("total", 0)
    cu_b = b.get("cpu", {}).get("usage", {}).get("total", 0)
    # system (kernel) usage not always present in cAdvisor v1.3 API
    ts_a = a.get("timestamp")
    ts_b = b.get("timestamp")
    # fall back to measured wall time
    # Parse RFC3339 timestamps
    def parse_ts(t):
        if not t:
            return None
        try:
            # Remove fractional seconds and timezone, parse basic format
            # Format: '2025-01-01T00:00:00.000000000Z' -> '2025-01-01T00:00:00'
            base = t.split(".")[0]
            # Remove timezone indicators (Z, +HH:MM, -HH:MM)
            if base.endswith("Z"):
                base = base[:-1]
            elif "+" in base:
                base = base.split("+")[0]
            elif base.count("-") > 2:
                # Check if last part is timezone (e.g., -05:00)
                parts = base.rsplit("-", 2)
                if len(parts) >= 3 and ":" in parts[-1]:
                    # Last part is timezone, remove it
                    base = "-".join(parts[:-1])
            return time.strptime(base, "%Y-%m-%dT%H:%M:%S")
        except Exception:
            return None
    try:
        parsed_a = parse_ts(ts_a)
        parsed_b = parse_ts(ts_b)
        if not parsed_a or not parsed_b:
            return 0
        ta = time.mktime(parsed_a)
        tb = time.mktime(parsed_b)
    except Exception:
        return 0
    dt = max(0.001, tb - ta)
    delta_ns = max(0, cu_b - cu_a)
    # Convert ns to seconds
    cpu_seconds = delta_ns / 1e9
    # Normalized CPU percent across available cores
    # Get CPU limit (in cores) from spec, default to 1 if not available
    cpu_limit = c.get("spec", {}).get("cpu", {}).get("limit", 0)
    if cpu_limit and cpu_limit > 0:
        # cAdvisor returns limit in cores (e.g., 2.0 for 2 cores)
        cpus = cpu_limit
    else:
        # Fallback: try to get from CPU count in spec
        cpus = max(1, c.get("spec", {}).get("cpu", {}).get("count", 1))
    # Calculate percentage: (cpu_time / wall_time) * 100, normalized by number of cores
    pct = min(100.0, (cpu_seconds / dt) * 100.0 / cpus)
    return int(round(pct * 100))  # hundredths

def get_state(c):
    # Running if last stat exists in last ~2 minutes
    stats = c.get("stats", [])
    if not stats:
        return 2
    ts = stats[-1].get("timestamp")
    try:
        t_last = time.mktime(time.strptime(ts.split(".")[0], "%Y-%m-%dT%H:%M:%S"))
        if time.time() - t_last < 120:
            return 1
    except Exception:
        pass
    return 2

def get_mem(c):
    stats = c.get("stats", [])
    if not stats:
        return 0, 0
    m = stats[-1].get("memory", {})
    usage = int(m.get("usage", 0))
    limit = int(c.get("spec", {}).get("memory", {}).get("limit", 0))
    return usage, limit

def get_name(c_id: str, c: dict) -> str:
    # Prefer aliases or name in spec
    n = c.get("aliases") or []
    if n:
        return n[0].lstrip("/")
    name = c.get("spec", {}).get("labels", {}).get("io.kubernetes.container.name")
    if name:
        return name
    # fallback: short ID
    return c.get("name", c_id)[:12].lstrip("/")

def get_restart_count(c: dict) -> int:
    # cAdvisor may not expose restart count consistently; default 0
    restart = (
        c.get("spec", {}).get("labels", {}).get("com.docker.compose.container-number")
    )
    try:
        return int(restart)
    except Exception:
        return 0

def emit_walk(rows):
    for r in rows:
        i = r["index"]
        print(f"{BASE_OID}.{i}.1")
        print("string")
        print(r["name"])

        print(f"{BASE_OID}.{i}.2")
        print("integer")
        print(r["state"])

        print(f"{BASE_OID}.{i}.3")
        print("integer")
        print(r["cpuHundredths"])

        print(f"{BASE_OID}.{i}.4")
        print("counter64")
        print(r["memBytes"])

        print(f"{BASE_OID}.{i}.5")
        print("counter64")
        print(r["memLimitBytes"])

        print(f"{BASE_OID}.{i}.6")
        print("counter32")
        print(r["restartCount"])
    print("END")
    sys.stdout.flush()

def build_rows(cadvisor_url: str):
    data = fetch_containers(cadvisor_url)
    rows = []
    # data is dict keyed by container ID
    for cid, c in sorted(data.items()):
        name = get_name(cid, c)
        idx = stable_index(name)
        state = get_state(c)
        cpu = calc_cpu_hundredths(c)
        mem, memlimit = get_mem(c)
        restarts = get_restart_count(c)
        rows.append(
            {
                "index": idx,
                "name": name,
                "state": state,
                "cpuHundredths": cpu,
                "memBytes": int(mem),
                "memLimitBytes": int(memlimit),
                "restartCount": int(restarts),
            }
        )
    # Ensure unique indices; if collision, increment
    used = set()
    for r in rows:
        i = r["index"]
        while i in used:
            i += 1
        used.add(i)
        r["index"] = i
    # Sort by index for stable walks
    rows.sort(key=lambda x: x["index"])
    return rows

def main():
    import os
    ap = argparse.ArgumentParser()
    default_url = os.environ.get("CADVISOR_URL", "")
    ap.add_argument("--url", default=default_url, help="cAdvisor base URL (or set CADVISOR_URL env var)")
    args = ap.parse_args()
    
    if not args.url:
        sys.stderr.write("ERROR: cAdvisor URL required (--url or CADVISOR_URL env var)\n")
        sys.exit(1)

    # pass_persist loop
    print("PING")
    sys.stdout.flush()

    while True:
        line = sys.stdin.readline()
        if not line:
            break
        cmd = line.strip()
        if cmd == "PING":
            print("PONG")
            sys.stdout.flush()
            continue
        if cmd.startswith("get "):
            # Handle single OID GET request
            oid = cmd[4:].strip()
            try:
                rows = build_rows(args.url)
                # Find matching OID
                found = False
                for r in rows:
                    idx = r["index"]
                    oid_base = f"{BASE_OID}.{idx}"
                    if oid == f"{oid_base}.1":
                        print(oid)
                        print("string")
                        print(r["name"])
                        found = True
                        break
                    elif oid == f"{oid_base}.2":
                        print(oid)
                        print("integer")
                        print(r["state"])
                        found = True
                        break
                    elif oid == f"{oid_base}.3":
                        print(oid)
                        print("integer")
                        print(r["cpuHundredths"])
                        found = True
                        break
                    elif oid == f"{oid_base}.4":
                        print(oid)
                        print("counter64")
                        print(r["memBytes"])
                        found = True
                        break
                    elif oid == f"{oid_base}.5":
                        print(oid)
                        print("counter64")
                        print(r["memLimitBytes"])
                        found = True
                        break
                    elif oid == f"{oid_base}.6":
                        print(oid)
                        print("counter32")
                        print(r["restartCount"])
                        found = True
                        break
                if not found:
                    print("NONE")
            except Exception as e:
                print("NONE")
            sys.stdout.flush()
            continue
        if cmd in ("getnext", "getbulk"):
            try:
                rows = build_rows(args.url)
            except Exception:
                # On error, return empty END to avoid blocking snmpd
                print("END")
                sys.stdout.flush()
                continue
            emit_walk(rows)

if __name__ == "__main__":
    main()