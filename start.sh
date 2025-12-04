#!/bin/sh
set -e

# Set defaults
CADVISOR_URL=${CADVISOR_URL:-http://cadvisor:8080}
SNMP_COMMUNITY=${SNMP_COMMUNITY:-public}

# Ensure config directory exists
mkdir -p /app/config

# Config file location (in mounted directory)
CONFIG_FILE="/app/config/snmpd.conf"

# Generate config file if it doesn't exist
if [ ! -f "$CONFIG_FILE" ]; then
    echo "Generating config file from template..."
    echo "CADVISOR_URL: ${CADVISOR_URL}"
    echo "SNMP_COMMUNITY: ${SNMP_COMMUNITY}"
    
    # Generate config file from template
    sed "s|__CADVISOR_URL__|${CADVISOR_URL}|g; s|__SNMP_COMMUNITY__|${SNMP_COMMUNITY}|g" \
        /app/snmpd.conf.template > "$CONFIG_FILE"
    
    # Verify file was created
    if [ ! -f "$CONFIG_FILE" ]; then
        echo "ERROR: Failed to create config file at $CONFIG_FILE" >&2
        exit 1
    fi
    
    echo "Generated config at $CONFIG_FILE ($(wc -c < "$CONFIG_FILE") bytes)"
else
    echo "Using existing config file at $CONFIG_FILE"
fi

# Validate config file exists
if [ ! -f "$CONFIG_FILE" ]; then
    echo "ERROR: Config file not found at $CONFIG_FILE" >&2
    exit 1
fi

# Show config summary
echo "Starting snmpd with config file: $CONFIG_FILE"
grep -E "^agentAddress|^rocommunity" "$CONFIG_FILE" || true

# Start SNMP daemon with custom config file
# -f = foreground
# -Lo = log to stdout
# -c = config file path
exec snmpd -f -Lo -c "$CONFIG_FILE"

