"""Shared requirements for tests against a disposable Redis Enterprise cluster."""

import json
import os
from urllib.parse import urlsplit


def enterprise_config():
    """Load an explicit Enterprise target; never fall back to local plaintext Redis."""
    path = os.environ.get("REDIS_ENTERPRISE_CONFIG")
    if not path:
        raise ValueError("Set REDIS_ENTERPRISE_CONFIG to a disposable Redis Enterprise cluster configuration file")
    try:
        with open(path) as file:
            config = json.load(file)
        uri = urlsplit(config["uri"])
        valid = uri.scheme == "rediss" and uri.hostname and uri.username and uri.password
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        raise ValueError("Invalid Redis Enterprise configuration file") from None
    if not valid:
        raise ValueError("Redis Enterprise integration tests require rediss:// with a username and password")
    if config.get("connection_type", "redis") != "redis":
        raise ValueError("Redis Enterprise configuration must use the native Redis connection type")
    return {**config, "batch_size": 2}


def flight_config():
    path = os.environ.get("FLIGHT_SQL_CONFIG")
    if not path:
        raise ValueError("Set FLIGHT_SQL_CONFIG to a TLS Flight SQL gateway backed by the configured Redis Enterprise cluster")
    try:
        with open(path) as file:
            config = json.load(file)
        uri = urlsplit(config["uri"])
        options = config.get("database_options", {})
        valid = uri.scheme == "grpc+tls" and uri.hostname and options.get("username") and options.get("password")
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        raise ValueError("Invalid Flight SQL configuration file") from None
    if not valid:
        raise ValueError("Flight SQL integration tests require grpc+tls:// and username/password database options")
    return {**config, "connection_type": "flight_sql"}
