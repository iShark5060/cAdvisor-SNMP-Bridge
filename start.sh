#!/bin/sh
set -e

# Set defaults
CADVISOR_URL=${CADVISOR_URL:-http://cadvisor:8080}
SNMP_COMMUNITY=${SNMP_COMMUNITY:-public}

# Ensure snmpd config directory exists
mkdir -p /etc/snmp

# Generate snmpd.conf from template
sed "s|__CADVISOR_URL__|${CADVISOR_URL}|g; s|__SNMP_COMMUNITY__|${SNMP_COMMUNITY}|g" \
    /app/snmpd.conf.template > /etc/snmp/snmpd.conf

# Start SNMP daemon in foreground
exec snmpd -f -Lo

