#!/bin/bash
# Utility script to inspect HTS Ticket Monitor health endpoint
set -e

HEALTH_URL="http://127.0.0.1:8080/health"

if command -v curl >/dev/null 2>&1; then
    if command -v jq >/dev/null 2>&1; then
        curl -s "$HEALTH_URL" | jq .
    elif command -v python3 >/dev/null 2>&1; then
        curl -s "$HEALTH_URL" | python3 -m json.tool
    elif command -v python >/dev/null 2>&1; then
        curl -s "$HEALTH_URL" | python -m json.tool
    else
        curl -s "$HEALTH_URL"
    fi
else
    echo "Error: curl is required to run check_health.sh" >&2
    exit 1
fi
