# cAdvisor-SNMP Bridge

A Docker container that bridges [cAdvisor](https://github.com/google/cadvisor) metrics to SNMP, enabling monitoring via LibreNMS or any SNMP-compatible monitoring system.

## Overview

This container runs `snmpd` with a custom Python script that:
- Fetches container statistics from cAdvisor API
- Exposes them via SNMP using the `pass_persist` mechanism
- Provides container metrics including CPU usage, memory usage, and container state

## Features

- **Lightweight**: Based on Alpine Linux
- **Dynamic**: Automatically discovers containers from cAdvisor
- **SNMP-Compatible**: Works with LibreNMS, Zabbix, Nagios, and other SNMP tools
- **Configurable**: Uses environment variables for easy configuration

## OID Structure

The custom OID tree is: `.1.3.6.1.4.1.424242.2.1` (private enterprise OID)

Each container is assigned a stable index, and the following metrics are available:

- `.{idx}.1` - Container name (string)
- `.{idx}.2` - Container state (integer: 1=running, 2=stopped)
- `.{idx}.3` - CPU usage in hundredths of percent (integer)
- `.{idx}.4` - Memory usage in bytes (counter64)
- `.{idx}.5` - Memory limit in bytes (counter64)
- `.{idx}.6` - Restart count (counter32)

## Quick Start

### Using Docker Compose (Recommended)

1. Edit `docker-compose.yml` and set:
   - `CADVISOR_URL`: URL to your cAdvisor instance
   - `SNMP_COMMUNITY`: SNMP read-only community string

2. Build and run:
   ```bash
   docker-compose up -d
   ```

### Using Docker Directly

1. Build the image:
   ```bash
   docker build -t cadvisor-snmp .
   ```

2. Run the container:
   ```bash
   docker run -d \
     --name cadvisor-snmp-bridge \
     --network host \
     -e CADVISOR_URL=http://localhost:8080 \
     -e SNMP_COMMUNITY=public \
     cadvisor-snmp
   ```

## Configuration

### Environment Variables

- **CADVISOR_URL** (required): URL to your cAdvisor instance
  - Example: `http://cadvisor:8080` (if in same Docker network)
  - Example: `http://localhost:8080` (if using host network)
  - Example: `https://mitsuko.darkavian.com:30110` (external URL)

- **SNMP_COMMUNITY** (optional, default: `public`): SNMP read-only community string
  - **Important**: Change this from the default for security!

### Network Modes

#### Host Network Mode (Default)
The container uses `network_mode: host` to bind directly to port 161 on the host. This is the simplest setup and works well when cAdvisor is also accessible on the host.

#### Bridge Network Mode
If cAdvisor is running in a Docker network, you can:

1. Comment out `network_mode: host` in `docker-compose.yml`
2. Uncomment the `networks` and `ports` sections
3. Ensure both containers are on the same network

Example:
```yaml
networks:
  - monitoring
ports:
  - "161:161/udp"
```

## Testing

### Test SNMP Query

```bash
# From the host or another machine with SNMP tools installed
snmpwalk -v 2c -c public localhost .1.3.6.1.4.1.424242.2.1

# Query a specific container's CPU usage
snmpget -v 2c -c public localhost .1.3.6.1.4.1.424242.2.1.{idx}.3
```

### Test cAdvisor Connection

```bash
# Enter the container
docker exec -it cadvisor-snmp-bridge sh

# Test the Python script manually
python3 /app/cadvisor.py --url http://your-cadvisor-url:8080
```

## Adding to LibreNMS

1. In LibreNMS, go to **Devices** → **Add Device**
2. Enter the hostname or IP address of the machine running this container
3. Set the SNMP version (2c recommended) and community string
4. LibreNMS should automatically discover the device

To add custom graphs:
1. Create a custom graph template in LibreNMS
2. Use the OID base `.1.3.6.1.4.1.424242.2.1`
3. Reference the specific metrics by index

## Troubleshooting

### Container won't start
- Check logs: `docker logs cadvisor-snmp-bridge`
- Verify `CADVISOR_URL` is correct and accessible from the container
- Ensure port 161/udp is not already in use (check with `netstat -ulnp | grep 161`)

### SNMP queries return no data
- Verify the SNMP community string matches your configuration
- Check if the container can reach cAdvisor: `docker exec cadvisor-snmp-bridge wget -O- $CADVISOR_URL/api/v1.3/docker`
- Review snmpd logs in the container: `docker exec cadvisor-snmp-bridge cat /var/log/snmpd.log` (if logging is enabled)

### No containers showing up
- Verify cAdvisor is exposing the `/api/v1.3/docker` endpoint
- Check that cAdvisor is actually monitoring containers (visit cAdvisor web UI)
- The script requires at least 2 stat samples to calculate CPU usage

## Development

### Running Locally

```bash
# Install dependencies
pip3 install requests

# Test the script
./cadvisor.py --url http://localhost:8080
```

### Script Improvements Made

- Fixed CPU calculation to properly normalize by CPU cores
- Added proper GET request handling (not just walks)
- Improved timestamp parsing for better compatibility
- Added environment variable support for cAdvisor URL
- Better error handling and edge cases

## Security Notes

- **Change the default SNMP community string!** The default is `public` which is insecure
- Consider using SNMPv3 with authentication if exposing over the network
- The container runs as root to bind to port 161; consider using capabilities if you want to run as non-root

## License

This project is provided as-is for bridging cAdvisor metrics to SNMP.

