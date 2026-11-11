import json
import subprocess
import sys
import selectors
from unittest.mock import Mock

import pytest
from airbyte_cdk.models import ConfiguredAirbyteCatalogSerializer, Status, Type

from source_redis_adbc.backend import ConnectorError, Table
from source_redis_adbc.source import SourceRedisAdbc
from source_redis_adbc.__main__ import message_to_json


def configured(mode="full_refresh"):
    return ConfiguredAirbyteCatalogSerializer.load({"streams": [{
        "stream": {"name": "items", "namespace": "public", "json_schema": {"type": "object", "properties": {"id": {"type": "string"}}}, "supported_sync_modes": ["full_refresh"]},
        "sync_mode": mode, "destination_sync_mode": "overwrite",
    }]})


def test_protocol_messages_and_selected_fields():
    backend = Mock()
    backend.discover.return_value = [(Table("redis", "public", "items"), {"type": "object", "properties": {"id": {"type": "string"}}})]
    backend.read.return_value = (item for item in [(Table("redis", "public", "items"), {"id": "1"})])
    source = SourceRedisAdbc(lambda config: backend)
    assert source.check(None, {"uri": "redis://localhost"}).status == Status.SUCCEEDED
    assert source.discover(None, {"uri": "redis://localhost"}).streams[0].name == "items"
    messages = list(source.read(None, {"uri": "redis://localhost"}, configured()))
    assert messages[0].type == Type.RECORD
    assert messages[0].record.data == {"id": "1"}
    assert messages[0].record.namespace == "public"
    assert messages[0].record.emitted_at > 0
    backend.read.assert_called_once_with([("public", "items", {"id": {"type": "string"}})])


def test_failed_check_status():
    backend = Mock()
    backend.check.side_effect = ConnectorError("safe error")
    source = SourceRedisAdbc(lambda config: backend)
    assert source.check(None, {"uri": "redis://localhost"}).status == Status.FAILED


def test_flight_shared_snapshot_fails_before_backend_creation():
    factory = Mock()
    source = SourceRedisAdbc(factory)
    config = {"uri": "grpc+tls://user:secret@localhost:1234", "connection_type": "flight_sql", "read_consistency": "shared_snapshot"}

    status = source.check(None, config)
    assert status.status == Status.FAILED
    assert "Shared snapshots are unsupported for Flight SQL" in status.message
    assert "secret" not in status.message
    with pytest.raises(ConnectorError, match="Shared snapshots are unsupported"):
        source.discover(None, config)
    with pytest.raises(ConnectorError, match="Shared snapshots are unsupported"):
        next(source.read(None, config, configured()))
    factory.assert_not_called()


@pytest.mark.parametrize("consistency", [None, "best_effort"])
@pytest.mark.parametrize("connection_type,uri", [
    ("redis", "rediss://localhost"),
    ("flight_sql", "grpc+tls://localhost:1234"),
])
def test_best_effort_consistency_preserves_full_refresh(consistency, connection_type, uri):
    backend = Mock()
    backend.discover.return_value = []
    backend.read.return_value = (item for item in [(Table("redis", "public", "items"), {"id": "1"})])
    source = SourceRedisAdbc(lambda config: backend)
    config = {"uri": uri, "connection_type": connection_type}
    if consistency is not None:
        config["read_consistency"] = consistency

    assert source.check(None, config).status == Status.SUCCEEDED
    assert source.discover(None, config).streams == []
    messages = list(source.read(None, config, configured()))
    assert [message.type for message in messages] == [Type.RECORD]
    assert messages[0].record.data == {"id": "1"}


@pytest.mark.parametrize("consistency", ["snapshot", "", True, None])
def test_invalid_consistency_is_rejected_without_connecting(consistency):
    factory = Mock()
    source = SourceRedisAdbc(factory)
    config = {"uri": "redis://localhost", "read_consistency": consistency}
    assert source.check(None, config).status == Status.FAILED
    with pytest.raises(ConnectorError, match="Invalid connector configuration"):
        source.discover(None, config)
    with pytest.raises(ConnectorError, match="Invalid connector configuration"):
        next(source.read(None, config, configured()))
    factory.assert_not_called()


def test_incremental_and_state_rejected():
    source = SourceRedisAdbc()
    with pytest.raises(ConnectorError, match="Only full_refresh"):
        list(source.read(None, {"uri": "redis://localhost"}, configured("incremental")))
    with pytest.raises(ConnectorError, match="state"):
        list(source.read(None, {"uri": "redis://localhost"}, configured(), state=[{"data": {"cursor": 1}}]))


def test_cli_spec_is_airbyte_json():
    result = subprocess.run([sys.executable, "-m", "source_redis_adbc", "spec"], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    messages = [json.loads(line) for line in result.stdout.splitlines()]
    spec = next(m["spec"] for m in messages if m["type"] == "SPEC")
    assert spec["connectionSpecification"]["properties"]["uri"]["airbyte_secret"] is True


def test_wire_serialization_preserves_nulls_in_record_data():
    from airbyte_cdk.models import AirbyteMessage, AirbyteRecordMessage
    data = {"id": "1", "nullable": None, "nested": {"x": None}, "list": [None, {"y": None}]}
    message = AirbyteMessage(type=Type.RECORD, record=AirbyteRecordMessage(stream="items", data=data, emitted_at=1))
    wire = json.loads(message_to_json(message))
    assert wire["record"]["data"] == data
    assert "namespace" not in wire["record"]
    assert set(wire) == {"type", "record"}


def test_sigterm_closes_active_record_generator(tmp_path):
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"uri": "redis://localhost"}))
    catalog = tmp_path / "catalog.json"
    from airbyte_cdk.models import ConfiguredAirbyteCatalogSerializer
    catalog.write_text(json.dumps(ConfiguredAirbyteCatalogSerializer.dump(configured())))
    marker = tmp_path / "closed"
    script = '''
import sys
from pathlib import Path
import source_redis_adbc.__main__ as cli
from source_redis_adbc.source import SourceRedisAdbc
from source_redis_adbc.backend import Table
marker = Path(sys.argv[1])
class Backend:
    def __init__(self, config): pass
    def read(self, selections):
        try:
            while True:
                yield Table("redis", "public", "items"), {"id": "1"}
        finally:
            marker.write_text("closed")
cli.SourceRedisAdbc = lambda: SourceRedisAdbc(Backend)
sys.argv = [sys.argv[0], *sys.argv[2:]]
cli.main()
'''
    process = subprocess.Popen([sys.executable, "-c", script, str(marker), "read", "--config", str(config), "--catalog", str(catalog)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            assert selector.select(timeout=10), "CLI did not emit a record"
        startup = []
        for _ in range(20):
            line = process.stdout.readline()
            if not line:
                break
            message = json.loads(line)
            startup.append(message)
            if message["type"] == "RECORD":
                break
        assert startup[-1]["type"] == "RECORD", startup
        process.terminate()
        process.communicate(timeout=10)
        assert process.returncode != 0
        assert marker.read_text() == "closed"
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()
