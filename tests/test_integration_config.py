import json

import pytest

from scripts.integration_config import enterprise_config, flight_config


@pytest.mark.parametrize("uri", [
    "redis://user:password@enterprise.example:12000/0",
    "rediss://enterprise.example:12000/0",
    "rediss://:password@enterprise.example:12000/0",
    "rediss://user:@enterprise.example:12000/0",
])
def test_native_integration_rejects_plaintext_or_missing_credentials(monkeypatch, tmp_path, uri):
    path = tmp_path / "redis.json"
    path.write_text(json.dumps({"uri": uri}))
    monkeypatch.setenv("REDIS_ENTERPRISE_CONFIG", str(path))
    with pytest.raises(ValueError, match="rediss:// with a username and password"):
        enterprise_config()


def test_native_integration_preserves_tls_configuration(monkeypatch, tmp_path):
    config = {"uri": "rediss://user:p%40ss@enterprise.example:12000/0", "database_options": {"ca": "/certs/ca.pem"}}
    path = tmp_path / "redis.json"
    path.write_text(json.dumps(config))
    monkeypatch.setenv("REDIS_ENTERPRISE_CONFIG", str(path))
    assert enterprise_config() == {**config, "batch_size": 2}


@pytest.mark.parametrize("loader,variable", [(enterprise_config, "REDIS_ENTERPRISE_CONFIG"), (flight_config, "FLIGHT_SQL_CONFIG")])
def test_explicit_integration_run_requires_configuration(monkeypatch, loader, variable):
    monkeypatch.delenv(variable, raising=False)
    with pytest.raises(ValueError, match=f"Set {variable}"):
        loader()


@pytest.mark.parametrize("uri,options", [
    ("grpc://gateway.example:31337", {"username": "user", "password": "secret"}),
    ("grpc+tls://gateway.example:31337", {}),
    ("grpc+tls://gateway.example:31337", {"username": "user"}),
])
def test_flight_integration_requires_tls_and_credentials(monkeypatch, tmp_path, uri, options):
    path = tmp_path / "flight.json"
    path.write_text(json.dumps({"uri": uri, "database_options": options}))
    monkeypatch.setenv("FLIGHT_SQL_CONFIG", str(path))
    with pytest.raises(ValueError, match="grpc\\+tls:// and username/password"):
        flight_config()


def test_enterprise_readiness_retries_transient_api_errors(monkeypatch):
    from scripts import enterprise_env

    calls = []
    def ready():
        calls.append(True)
        if len(calls) == 1:
            raise enterprise_env.TransientAPIError('HTTP 503')
        return True
    monkeypatch.setattr(enterprise_env.time, 'sleep', lambda seconds: None)
    assert enterprise_env.wait('test API', ready, seconds=1)
    assert len(calls) == 2


def test_enterprise_readiness_does_not_hide_authentication_failure():
    from scripts import enterprise_env

    def denied():
        raise RuntimeError('HTTP 401')
    with pytest.raises(RuntimeError, match='401'):
        enterprise_env.wait('test API', denied, seconds=1)
