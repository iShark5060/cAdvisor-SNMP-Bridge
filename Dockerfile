FROM alpine:latest

# Install required packages
RUN apk add --no-cache \
    net-snmp \
    net-snmp-tools \
    python3 \
    py3-requests

# Create directory for scripts and config
RUN mkdir -p /app

# Copy the Python script
COPY cadvisor.py /app/cadvisor.py
RUN chmod +x /app/cadvisor.py

# Copy SNMP configuration template
COPY snmpd.conf.template /app/snmpd.conf.template

# Copy startup script
COPY start.sh /app/start.sh
RUN chmod +x /app/start.sh && \
    sed -i 's/\r$//' /app/start.sh 2>/dev/null || true

# Expose SNMP port
EXPOSE 161/udp

# Start SNMP daemon
CMD ["/app/start.sh"]

