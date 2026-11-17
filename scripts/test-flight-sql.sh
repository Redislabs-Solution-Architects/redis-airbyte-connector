#!/usr/bin/env bash
# Test a TLS gateway backed by the configured Redis Enterprise cluster.
set -euo pipefail
cd "$(dirname "$0")/.."
: "${REDIS_ENTERPRISE_CONFIG:?Set a native Redis Enterprise TLS configuration file}"
: "${FLIGHT_SQL_CONFIG:?Set a Redis-backed TLS Flight SQL configuration file}"
python -m pytest -q tests/test_flight_sql.py
