import json
import uuid
from contextlib import closing

import pytest
from airbyte_cdk.models import (
    AirbyteMessageSerializer,
    ConfiguredAirbyteCatalogSerializer,
    Status,
)

from scripts.integration_config import enterprise_config
from destination_redis_adbc.destination import DestinationRedisAdbc
from destination_redis_adbc.protocol import message_to_json
from source_redis_adbc.backend import ConnectorError, connect, quote
from source_redis_adbc.source import SourceRedisAdbc

pytestmark = pytest.mark.integration


PROPERTIES = {
    "id": {"type": "integer"},
    "label": {"type": ["null", "string"]},
    "amount": {"type": "string"},
    "binary": {"type": ["null", "string"], "contentEncoding": "base64"},
    "created": {"type": ["null", "string"], "format": "date-time", "airbyte_type": "timestamp_without_timezone"},
    "clock": {"type": ["null", "string"], "format": "time"},
    "nested": {"type": ["null", "object"], "properties": {"count": {"type": "integer"}}},
    "ids": {"type": ["null", "array"], "items": {"type": ["null", "integer"]}},
}


@pytest.fixture
def destination_config():
    config = {**enterprise_config(), "schema": "airbyte_dest_" + uuid.uuid4().hex[:12]}
    try:
        yield config
    finally:
        with closing(connect(config)) as connection, closing(connection.cursor()) as cursor:
            cursor.execute(f'DROP SCHEMA IF EXISTS {quote(config["schema"])} CASCADE')


def configured(mode="append", properties=PROPERTIES):
    return ConfiguredAirbyteCatalogSerializer.load(
        {
            "streams": [
                {
                    "stream": {
                        "name": "items",
                        "namespace": "public",
                        "json_schema": {"type": "object", "properties": properties},
                        "supported_sync_modes": ["full_refresh"],
                    },
                    "sync_mode": "full_refresh",
                    "destination_sync_mode": mode,
                }
            ]
        }
    )


def message(n, **data):
    return AirbyteMessageSerializer.load(
        {
            "type": "RECORD",
            "record": {
                "stream": "items",
                "namespace": "public",
                "data": {"id": n, "amount": "12345678901234567890.123456789", **data},
                "emitted_at": 1,
            },
        }
    )


def state(n):
    return AirbyteMessageSerializer.load({"type": "STATE", "state": {"data": {"cursor": n, "nullable": None}}})


def read_back(config):
    source = SourceRedisAdbc()
    source_config = {"uri": config["uri"], "database_options": config.get("database_options", {}), "schemas": [config["schema"]], "batch_size": 2}
    discovered = source.discover(None, source_config)
    stream = next(s for s in discovered.streams if s.name == "items")
    catalog = ConfiguredAirbyteCatalogSerializer.load(
        {
            "streams": [
                {
                    "stream": {
                        "name": stream.name,
                        "namespace": stream.namespace,
                        "json_schema": stream.json_schema,
                        "supported_sync_modes": ["full_refresh"],
                    },
                    "sync_mode": "full_refresh",
                    "destination_sync_mode": "overwrite",
                }
            ]
        }
    )
    return [m.record.data for m in source.read(None, source_config, catalog)]


def test_destination_check_and_typed_round_trip(destination_config):
    config = destination_config
    destination = DestinationRedisAdbc()
    assert destination.check(None, config).status == Status.SUCCEEDED
    incoming = [
        message(
            2**60,
            label="first",
            binary="AP8=",
            created="2026-11-01T01:02:03.123456789",
            clock="01:02:03.123456789",
            nested={"count": 7},
            ids=[1, None, 3],
        ),
        state(1),
        message(2, label=None),
        message(3, label="third"),
        state(3),
        message(4),
        message(5),
    ]
    acks = list(destination.write(config, configured(), incoming))
    assert [json.loads(message_to_json(m))["state"]["data"] for m in acks] == [
        {"cursor": 1, "nullable": None},
        {"cursor": 3, "nullable": None},
    ]
    rows = {row["id"]: row for row in read_back(config)}
    first = rows[str(2**60)]
    assert first["amount"] == "12345678901234567890.123456789"
    assert first["binary"] == "AP8="
    assert first["created"] == "2026-11-01T01:02:03.123456789"
    assert first["clock"] == "01:02:03.123456789"
    assert first["nested"] == {"count": "7"}
    assert first["ids"] == ["1", None, "3"]
    assert rows["2"]["label"] is None
    assert len(rows) == 5
    # Append retains previous records; it intentionally does not deduplicate.
    list(destination.write(config, configured(), [message(6), state(6)]))
    assert len(read_back(config)) == 6


def test_failed_overwrite_preserves_prior_data_and_cleans_stage(destination_config):
    config = destination_config
    destination = DestinationRedisAdbc()
    list(destination.write(config, configured(), [message(99)]))
    gen = destination.write(config, configured("overwrite"), [message(1), message(2), state(2), message("invalid-id")])
    with pytest.raises(ConnectorError):
        next(gen)
    assert [row["id"] for row in read_back(config)] == ["99"]
    source = SourceRedisAdbc()
    names = [s.name for s in source.discover(None, {"uri": config["uri"], "database_options": config.get("database_options", {}), "schemas": [config["schema"]]}).streams]
    assert names == ["items"]
    # Retry a complete overwrite and confirm the final target replaces old data.
    acks = list(destination.write(config, configured("overwrite"), [message(1), state(1), message(2), state(2)]))
    assert [m.state.data["cursor"] for m in acks] == [1, 2]
    assert sorted(row["id"] for row in read_back(config)) == ["1", "2"]


def test_empty_overwrite_and_cancelled_input(destination_config):
    config = destination_config
    destination = DestinationRedisAdbc()
    list(destination.write(config, configured(), [message(99)]))

    def interrupted():
        yield message(1)
        yield message(2)
        yield state(2)
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        list(destination.write(config, configured("overwrite"), interrupted()))
    assert [row["id"] for row in read_back(config)] == ["99"]
    assert len(list(destination.write(config, configured("overwrite"), [state(0)]))) == 1
    assert read_back(config) == []
