#!/usr/bin/env python3
"""
SNMP pass_persist script for cAdvisor metrics.
Exposes container metrics via SNMP using the pass_persist protocol.
"""
import sys
import time
import argparse
import hashlib

try:
    import requests
except ImportError:
    print("ERROR: 'requests' module is not installed.", file=sys.stderr)
    print("Install it with: pip3 install requests", file=sys.stderr)
    sys.exit(1)

BASE_OID = ".1.3.6.1.4.1.424242.2.1"

def stable_index(name: str) -> int:
    """Generate a stable numeric index from container name."""
    h = hashlib.sha1(name.encode("utf-8")).digest()
    return int.from_bytes(h[:2], "big") or 1

def fetch_containers(cadvisor_url: str, timeout: float = 2.0):
    """Fetch container data from cAdvisor API."""
    url = cadvisor_url.rstrip("/") + "/api/v1.3/docker"
    r = requests.get(url, timeout=timeout)
    r.raise_for_status()
    return r.json()

def calc_cpu_hundredths(c):
    """Calculate CPU usage percentage in hundredths (0-10000)."""
    stats = c.get("stats", [])
    if len(stats) < 2:
        return 0

    a = stats[-2]
    b = stats[-1]
    cu_a = a.get("cpu", {}).get("usage", {}).get("total", 0)
    cu_b = b.get("cpu", {}).get("usage", {}).get("total", 0)
    ts_a = a.get("timestamp")
    ts_b = b.get("timestamp")

    def parse_ts(t):
        """Parse RFC3339 timestamp."""
        if not t:
            return None
        try:
            base = t.split(".")[0]
            if base.endswith("Z"):
                base = base[:-1]
            elif "+" in base:
                base = base.split("+")[0]
            elif base.count("-") > 2:
                parts = base.rsplit("-", 2)
                if len(parts) >= 3 and ":" in parts[-1]:
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
    cpu_seconds = delta_ns / 1e9
    cpu_limit = c.get("spec", {}).get("cpu", {}).get("limit", 0)
    if cpu_limit and cpu_limit > 0:
        cpus = cpu_limit
    else:
        cpus = max(1, c.get("spec", {}).get("cpu", {}).get("count", 1))
    pct = min(100.0, (cpu_seconds / dt) * 100.0 / cpus)
    return int(round(pct * 100))

def get_state(c):
    """Get container state: 1=running, 2=stopped."""
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

def get_restart_count(c: dict) -> int:
    """Get restart count from labels."""
    restart = c.get("spec", {}).get("labels", {}).get("com.docker.compose.container-number")
    try:
        return int(restart)
    except Exception:
        return 0

def build_rows(cadvisor_url: str):
    """Build rows of container metrics from cAdvisor data."""
    try:
        data = fetch_containers(cadvisor_url)
    except Exception as e:
        print(f"ERROR: Failed to fetch from cAdvisor: {e}", file=sys.stderr)
        sys.stderr.flush()
        return []

    rows = []
    for cid, c in sorted(data.items()):
        try:
            name = get_name(cid, c)
            idx = stable_index(name)
            state = get_state(c)
            cpu = calc_cpu_hundredths(c)
            mem, memlimit = get_mem(c)
            restarts = get_restart_count(c)
            rows.append({
                "index": idx,
                "name": name,
                "state": state,
                "cpuHundredths": cpu,
                "memBytes": int(mem),
                "memLimitBytes": int(memlimit),
                "restartCount": int(restarts),
            })
        except Exception as e:
            print(f"ERROR: Failed to process container {cid}: {e}", file=sys.stderr)
            sys.stderr.flush()
            continue

    used = set()
    for r in rows:
        i = r["index"]
        while i in used:
            i += 1
        used.add(i)
        r["index"] = i
    
    rows.sort(key=lambda x: x["index"])
    return rows

def oid_to_tuple(oid_str):
    """Convert OID string to tuple of integers for comparison."""
    if not oid_str:
        return tuple()
    oid_str = oid_str.lstrip(".")
    if not oid_str:
        return tuple()
    parts = [x for x in oid_str.split(".") if x]
    try:
        return tuple(int(x) for x in parts)
    except (ValueError, AttributeError):
        return tuple()

def normalize_oid(oid):
    """Normalize OID by removing leading dot."""
    return oid.lstrip(".") if oid.startswith(".") else oid

def main():
    import os
    ap = argparse.ArgumentParser(description="SNMP pass_persist script for cAdvisor metrics")
    default_url = os.environ.get("CADVISOR_URL", "http://127.0.0.1:8080")
    ap.add_argument("--url", default=default_url,
                    help="cAdvisor base URL (or set CADVISOR_URL env var)")
    ap.add_argument("--test", action="store_true",
                    help="Test mode: fetch data and display it, then exit")
    args = ap.parse_args()

    if args.test:
        print("Testing cAdvisor connection...", file=sys.stderr)
        try:
            rows = build_rows(args.url)
            print(f"✓ Successfully connected to cAdvisor at {args.url}", file=sys.stderr)
            print(f"✓ Found {len(rows)} containers\n", file=sys.stderr)
            for r in rows:
                print(f"Container: {r['name']}")
                print(f"  Index: {r['index']}")
                print(f"  State: {'Running' if r['state'] == 1 else 'Stopped'}")
                print(f"  CPU: {r['cpuHundredths']/100:.2f}%")
                print(f"  Memory: {r['memBytes']/1024/1024:.2f} MB / {r['memLimitBytes']/1024/1024:.2f} MB")
                print(f"  Restarts: {r['restartCount']}")
                print()
            print("✓ Test completed successfully!", file=sys.stderr)
            return
        except Exception as e:
            print(f"✗ Error: {e}", file=sys.stderr)
            import traceback
            traceback.print_exc(file=sys.stderr)
            sys.exit(1)

    cache_data = None
    cache_time = 0
    cache_ttl = 5.0

    def get_cached_rows():
        """Get cached rows or fetch fresh data if cache expired."""
        nonlocal cache_data, cache_time
        now = time.time()
        if cache_data is None or (now - cache_time) > cache_ttl:
            cache_data = build_rows(args.url)
            cache_time = now
        return cache_data

    def build_oid_map(rows):
        """Build a map of OID -> (type, value) for fast lookup."""
        oid_map = {}
        for r in rows:
            idx = r["index"]
            base = f"{BASE_OID}.{idx}"
            oid_map[f"{base}.1"] = ("string", r["name"])
            oid_map[f"{base}.2"] = ("integer", r["state"])
            oid_map[f"{base}.3"] = ("integer", r["cpuHundredths"])
            oid_map[f"{base}.4"] = ("counter64", r["memBytes"])
            oid_map[f"{base}.5"] = ("counter64", r["memLimitBytes"])
            oid_map[f"{base}.6"] = ("counter32", r["restartCount"])
        return oid_map

    while True:
        try:
            line = sys.stdin.readline()
            if not line:
                break
            
            cmd = line.strip()
            if not cmd:
                continue

            if cmd == "PING":
                print("PONG")
                sys.stdout.flush()
                continue

            if cmd.startswith("get "):
                oid = normalize_oid(cmd[4:].strip())
                try:
                    rows = get_cached_rows()
                    oid_map = build_oid_map(rows)
                    
                    if oid in oid_map:
                        oid_type, oid_value = oid_map[oid]
                        print(oid)
                        print(oid_type)
                        print(oid_value)
                    else:
                        print("NONE")
                except Exception as e:
                    print(f"ERROR in get: {e}", file=sys.stderr)
                    sys.stderr.flush()
                    print("NONE")
                sys.stdout.flush()
                continue

            if cmd.startswith("getnext") or cmd.startswith("getbulk"):
                parts = cmd.split()
                requested_oid = None

                if cmd.startswith("getbulk"):
                    if len(parts) >= 4:
                        requested_oid = parts[-1].strip()
                    else:
                        line1 = sys.stdin.readline()
                        if line1:
                            line2 = sys.stdin.readline()
                            if line2:
                                oid_line = sys.stdin.readline()
                                if oid_line:
                                    requested_oid = oid_line.strip()
                elif len(parts) >= 2:
                    requested_oid = parts[-1].strip()
                else:
                    oid_line = sys.stdin.readline()
                    if oid_line:
                        requested_oid = oid_line.strip()

                if not requested_oid:
                    print("END")
                    sys.stdout.flush()
                    continue

                try:
                    rows = get_cached_rows()
                    if not rows:
                        print("END")
                        sys.stdout.flush()
                        continue

                    all_oids = []
                    for r in rows:
                        idx = r["index"]
                        base = f"{BASE_OID}.{idx}"
                        all_oids.append((f"{base}.1", "string", r["name"]))
                        all_oids.append((f"{base}.2", "integer", r["state"]))
                        all_oids.append((f"{base}.3", "integer", r["cpuHundredths"]))
                        all_oids.append((f"{base}.4", "counter64", r["memBytes"]))
                        all_oids.append((f"{base}.5", "counter64", r["memLimitBytes"]))
                        all_oids.append((f"{base}.6", "counter32", r["restartCount"]))

                    requested_tuple = oid_to_tuple(requested_oid)
                    base_tuple = oid_to_tuple(BASE_OID)

                    sorted_oids = sorted(all_oids, key=lambda x: oid_to_tuple(x[0]))
                    next_oid = None

                    if requested_tuple == base_tuple:
                        if sorted_oids:
                            next_oid = sorted_oids[0]
                    else:
                        for oid_str, oid_type, oid_value in sorted_oids:
                            oid_tuple = oid_to_tuple(oid_str)
                            if oid_tuple > requested_tuple:
                                next_oid = (oid_str, oid_type, oid_value)
                                break

                    if next_oid:
                        output_oid = normalize_oid(next_oid[0])
                        print(output_oid)
                        print(next_oid[1])
                        print(next_oid[2])
                    else:
                        print("END")

                except Exception as e:
                    print(f"ERROR in getnext/getbulk: {e}", file=sys.stderr)
                    import traceback
                    traceback.print_exc(file=sys.stderr)
                    sys.stderr.flush()
                    print("END")

                sys.stdout.flush()
                continue

            print("NONE")
            sys.stdout.flush()
            continue

        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"ERROR in main loop: {e}", file=sys.stderr)
            import traceback
            traceback.print_exc(file=sys.stderr)
            sys.stderr.flush()
            continue

if __name__ == "__main__":
    main()