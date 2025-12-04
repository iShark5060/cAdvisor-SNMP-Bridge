#!/bin/sh
# Quick script to copy the config from /etc/snmp to the mounted directory
# Run this inside the container: docker exec cadvisor-snmp-bridge sh /tmp/copy-config.sh

CONFIG_FILE="/app/config/snmpd.conf"
SNMPD_CONFIG="/etc/snmp/snmpd.conf"

if [ -f "$SNMPD_CONFIG" ]; then
    cp "$SNMPD_CONFIG" "$CONFIG_FILE"
    echo "Copied config from $SNMPD_CONFIG to $CONFIG_FILE"
    ls -la "$CONFIG_FILE"
else
    echo "Config file not found at $SNMPD_CONFIG"
fi

