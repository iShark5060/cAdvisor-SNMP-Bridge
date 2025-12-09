# cAdvisor SNMP Bridge for LibreNMS

Bridge cAdvisor container metrics to LibreNMS via SNMP using `snmpd extend`. This allows LibreNMS to monitor Docker containers running on TrueNAS (or technically any Linux system) using the built-in Docker application.

## Features

- Exposes Docker container metrics via SNMP
- Compatible with LibreNMS Docker application
- Provides CPU, Memory, Process count, Uptime, and Filesystem metrics
- Automatic container state detection
- Works with cAdvisor running in Docker or directly on host

## Prerequisites

- TrueNAS (or any Linux system with `snmpd` and Python3)
- Python3 with `requests` module
- cAdvisor running (via Docker or host) and accessible to the snmpd user
- LibreNMS server with SNMP access to TrueNAS (or other Linux system)

## Installation on TrueNAS

### Step 1: Install cAdvisor (if not already running)

As the time of writing cAdvisor is not available via the TrueNAS App Catalog.
Use the `compose.yaml` in this repo for a simple cAdvisor container that you can use to install a custom app.
You could also use the GUI and fill in the information, but I find it easier to use the yaml importer. Additionally there is currently a Bug in TrueNAS (will probably be fixed with 25.10.1) that doesn't let you mound certain folders with the GUI.

Adjust the exposed port as needed. I used `30110` in my setup.

**Note:** cAdvisor should be accessible at `http://127.0.0.1:30110` (or your configured port).
Test if the service is running and if all your containers show up as expected.

### Step 2: Copy Scripts to TrueNAS

Copy the script to some directory that the root user can read.
I've added a new Dataset in my "Container" Pool:

```
root@Mitsuko[/mnt/Container/cAdvisor-SNMP]# ls -al
total 26
drwxr-xr-x  2 root Debian-snmp     4 Dec  5 12:17 .
drwxr-xr-x 15 root root           16 Dec  4 17:47 ..
-rw-r--r--  1 root Debian-snmp 12588 Dec  5 12:17 cadvisor-extend.py
-rw-r--r--  1 root Debian-snmp 12858 Dec  5 11:43 cadvisor.py
root@Mitsuko[/mnt/Container/cAdvisor-SNMP]#
```

honestly not sure if the files need to be read by the Docker-snmp user, since the snmpd is run as root, but meh .. better to be safe.

### Step 3: Configure snmpd

Edit `/etc/snmp/snmpd.conf` via the GUI (System -> Services -> SNMP -> Edit) and add the following line to the Auxiliary Parameters section:

```conf
extend docker /usr/bin/python3 /mnt/Container/cAdvisor-SNMP/cadvisor-extend.py --url http://127.0.0.1:30110
```

**Important:** Replace `http://127.0.0.1:30110` with your actual cAdvisor URL if different. And also use the correct mount path for your setup.

### Step 4: Restart snmpd

automatically done if you press "save" in the GUI, otherwise you can manually restart it.

```bash
systemctl restart snmpd
```

## Verification

### Test Script Directly

```bash
# Test the script
/usr/bin/python3 /mnt/Container/cAdvisor-SNMP/cadvisor-extend.py --url http://127.0.0.1:30110 | python3 -m json.tool
```

You should see JSON output with container metrics.
Example output:

```
[...]
    {
        "container": "jellyfin",
        "cpu": 0.05,
        "pids": 1,
        "memory": {
            "perc": 0.0,
            "used": "521.17MiB",
            "limit": "0B"
        },
        "state": {
            "status": "running",
            "uptime": 64466
        },
        "size": {
            "size_rw": null,
            "size_root_fs": 365568
        }
    },
[...]
```

### Test via SNMP

```bash
# From TrueNAS or LibreNMS server
snmpwalk -v2c -c <your-community> <truenas-ip> NET-SNMP-EXTEND-MIB::nsExtendOutput1Table | grep docker
```

You should see JSON output with container data.

## LibreNMS Integration

- Add your TrueNAS Server as a new device, if not present already
- Edit the device and enable "Docker" Application

LibreNMS will poll every 5 minutes by default. You can force an immediate poll:

```bash
# On LibreNMS server
cd /opt/librenms
su librenms
lnms device:poll -m applications <device-id>
```

After polling:
- Go to **Devices** → Your TrueNAS Device → **Applications** → **Docker**
- You should see:
  - Container list
  - CPU usage graphs
  - Memory usage graphs
  - Container states
  - Uptime information

### Step 5: Configure Alarms

And this is where we come to a limitation of the Docker App implementation in LibreNMS.
I haven't found a way yet to tell WHICH container is not running, only that SOME container is not running.

- Edit Device
- Alert Rules
- Create new Alert Rule
- Name the Alert something like "Docker Container Down"
- then place some dummy rule like "device.device_id" "not equal" "0"
- under advanced, activate Override SQL and paste the following:

```
SELECT * FROM devices, applications, application_metrics 
WHERE (devices.device_id = ? 
  AND devices.device_id = applications.device_id 
  AND applications.app_id = application_metrics.app_id
  AND applications.app_type = 'docker') 
  AND application_metrics.metric = 'total_running'
  AND application_metrics.value_prev IS NOT NULL
  AND application_metrics.value < application_metrics.value_prev
```
if you have a container that runs and then exits, this will misfire. Keep that in mind.

## Configuration

### Environment Variables

You can set `CADVISOR_URL` environment variable instead of using `--url`:

```bash
# In snmpd.conf, you can use:
extend docker env CADVISOR_URL=http://127.0.0.1:30110 /usr/bin/python3 /mnt/Container/cAdvisor-SNMP/cadvisor-extend.py
```

## Metrics Provided

The script provides the following metrics to LibreNMS:

| Metric | Description | Unit |
|--------|-------------|------|
| `cpu` | CPU usage percentage | % |
| `pids` | Process count | count |
| `memory.perc` | Memory usage percentage | % |
| `memory.used` | Memory used | bytes (formatted) |
| `memory.limit` | Memory limit | bytes (formatted) |
| `state.status` | Container state | running/exited/paused/etc |
| `state.uptime` | Container uptime | seconds |
| `size.size_rw` | Read-write layer size | bytes |
| `size.size_root_fs` | Root filesystem size | bytes |

## The "other" script

The `cadvisor.py` script was a test using pass_persist instead of extend. Works fine, but I found implementing it into LibreNMS with the Docker application easier.
However, since the Docker Application is deprecated, maybe it becomes relevant some time in the future.

to use this one, simply add the following line to the `snmpd.conf`:

```
pass_persist .1.3.6.1.4.1.424242.2.1 /usr/bin/python3 /mnt/Container/cAdvisor-SNMP/cadvisor.py --url http://127.0.0.1:30110
```

adjust path and ip/port as needed, obviously.

## Screenshot (from LibreNMS)

<img width="961" height="789" alt="image" src="https://github.com/user-attachments/assets/93d258a7-0d4b-41db-9ae9-f4d0ef66a24e" />

## Acknowledgments

- [cAdvisor](https://github.com/google/cadvisor) - Container resource usage monitoring
- [LibreNMS](https://www.librenms.org/) - Network monitoring system
- [Net-SNMP](https://net-snmp.sourceforge.io/) - SNMP implementation
