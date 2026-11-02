import io
import json
import subprocess
import sys
from contextlib import contextmanager
from unittest.mock import Mock

import pyarrow as pa
import pytest
from airbyte_cdk.models import (
    AirbyteMessageSerializer,
    ConfiguredAirbyteCatalogSerializer,
)

from destination_redis_adbc.destination import DestinationRedisAdbc
from destination_redis_adbc.protocol import message_to_json
from destination_redis_adbc.writer import RedisWriter, plans_for
from source_redis_adbc.backend import ConnectorError, Table

CONFIG = {"uri": "redis://localhost", "batch_size": 2}


def catalog(mode="append", name="items", namespace="public", properties=None):
    return ConfiguredAirbyteCatalogSerializer.load(
        {
            "streams": [
                {
                    "stream": {
                        "name": name,
                        "namespace": namespace,
                        "json_schema": {"type": "object", "properties": properties or {"id": {"type": "integer"}}},
                        "supported_sync_modes": ["full_refresh"],
                    },
                    "sync_mode": "full_refresh",
                    "destination_sync_mode": mode,
                }
            ]
        }
    )


def record(n, stream="items", namespace="public"):
    return AirbyteMessageSerializer.load(
        {"type": "RECORD", "record": {"stream": stream, "namespace": namespace, "data": {"id": n}, "emitted_at": 1}}
    )


def state(n=1):
    return AirbyteMessageSerializer.load({"type": "STATE", "state": {"data": {"cursor": n, "nullable": None}}})


class Backend:
    def __init__(self, config, existing=None):
        self.config = config
        self.conn = Mock()
        self.cursor = self.conn.cursor.return_value
        self.cursor.adbc_ingest.side_effect = lambda name, data, **kw: data.num_rows
        self.closed = False
        self.existing = existing or {}

    @contextmanager
    def connection(self):
        try:
            yield self.conn
        finally:
            self.closed = True

    def tables(self, connection):
        return self.existing

    def schema(self, connection, table):
        return pa.schema([("id", pa.int64())])


def writer(existing=False, **config):
    backend = Backend(
        {**CONFIG, **config}, {("airbyte", "items"): Table("redis", "airbyte", "items")} if existing else None
    )
    return RedisWriter(backend.config, lambda conf: backend), backend


def test_append_flushes_before_state_and_bounds_batches():
    w, b = writer()
    inputs = iter([record(1), state(1), record(2), record(3), state(3), record(4)])
    output = w.write(catalog(), inputs)
    assert next(output).state.data["cursor"] == 1
    calls = b.cursor.adbc_ingest.call_args_list
    assert [(call.kwargs["mode"], call.args[1].num_rows) for call in calls] == [("create_append", 0), ("append", 1)]
    assert [m.state.data["cursor"] for m in output] == [3]
    assert [call.args[1].num_rows for call in b.cursor.adbc_ingest.call_args_list] == [0, 1, 2, 1]
    assert b.closed


def test_ingest_failure_never_acknowledges_state():
    w, b = writer()
    b.cursor.adbc_ingest.side_effect = [0, RuntimeError("write failed")]
    gen = w.write(catalog(), [record(1), state()])
    with pytest.raises(RuntimeError):
        next(gen)
    assert b.closed


def test_partial_batch_count_fails_without_ack():
    w, b = writer()
    b.cursor.adbc_ingest.return_value = 0
    b.cursor.adbc_ingest.side_effect = None
    with pytest.raises(ConnectorError, match="complete batch"):
        list(w.write(catalog(), [record(1), state()]))


def test_overwrite_defers_all_states_until_eof_and_promotion():
    w, b = writer(existing=True)
    events = []

    def inputs():
        yield record(1)
        yield state(1)
        events.append("after-state")
        # No target rename may happen before the input is exhausted.
        assert not any("ALTER TABLE" in call.args[0] for call in b.cursor.execute.call_args_list)
        yield record(2)
        yield state(2)
        events.append("eof")

    output = w.write(catalog("overwrite"), inputs())
    first = next(output)
    assert first.state.data["cursor"] == 1
    assert events == ["after-state", "eof"]
    sql = [call.args[0] for call in b.cursor.execute.call_args_list]
    assert any('ALTER TABLE "airbyte"."items" RENAME TO "airbyte_backup_' in command for command in sql)
    assert any('RENAME TO "items"' in command for command in sql)
    assert [m.state.data["cursor"] for m in output] == [2]


def test_bad_overwrite_record_keeps_existing_target_and_cleans_stage():
    w, b = writer(existing=True)
    with pytest.raises(ConnectorError):
        list(w.write(catalog("overwrite"), [record(1), state(), record("secret-invalid")]))
    sql = [call.args[0] for call in b.cursor.execute.call_args_list]
    assert not any("ALTER TABLE" in command for command in sql)
    assert any("DROP TABLE IF EXISTS" in command and "airbyte_stage_" in command for command in sql)
    assert b.closed


def test_failed_promotion_restores_backup_and_does_not_ack():
    w, b = writer(existing=True)
    failed = False

    def execute(sql):
        nonlocal failed
        if "airbyte_stage_" in sql and 'RENAME TO "items"' in sql and not failed:
            failed = True
            raise RuntimeError("injected rename failure")

    b.cursor.execute.side_effect = execute
    with pytest.raises(RuntimeError):
        list(w.write(catalog("overwrite"), [record(1), state()]))
    sql = [call.args[0] for call in b.cursor.execute.call_args_list]
    assert any("airbyte_backup_" in command and 'RENAME TO "items"' in command for command in sql)
    assert not any(command.startswith("DROP TABLE ") and "airbyte_backup_" in command for command in sql)


def test_empty_overwrite_creates_and_promotes_empty_table():
    w, b = writer(existing=True)
    assert list(w.write(catalog("overwrite"), [])) == []
    assert b.cursor.adbc_ingest.call_args_list[0].args[1].num_rows == 0
    assert any('RENAME TO "items"' in c.args[0] for c in b.cursor.execute.call_args_list)


def test_max_state_spool_and_unknown_stream_rejected():
    w, _ = writer(max_state_bytes=1)
    with pytest.raises(ConnectorError, match="max_state_bytes"):
        list(w.write(catalog("overwrite"), [state()]))
    w, _ = writer()
    with pytest.raises(ConnectorError, match="unconfigured"):
        list(w.write(catalog(), [record(1, stream="wrong")]))


def test_collision_and_dedup_rejected_before_writes():
    c = catalog()
    c.streams += catalog(namespace="other").streams
    with pytest.raises(ConnectorError, match="target"):
        plans_for(CONFIG, c)
    assert len(plans_for({**CONFIG, "use_stream_namespace": True}, c)) == 2
    with pytest.raises(ConnectorError, match="deduplication"):
        plans_for(CONFIG, catalog("append_dedup"))


@pytest.mark.parametrize(
    "wire",
    [
        {"type": "STATE", "state": {"data": {"x": None, "nested": {"null": None}}}},
        {
            "type": "STATE",
            "state": {
                "type": "STREAM",
                "stream": {"stream_descriptor": {"name": "items"}, "stream_state": {"x": None}},
            },
        },
        {
            "type": "STATE",
            "state": {
                "type": "GLOBAL",
                "global": {
                    "shared_state": {"x": None},
                    "stream_states": [{"stream_descriptor": {"name": "items"}, "stream_state": {"x": None}}],
                },
            },
        },
    ],
)
def test_state_acknowledgements_preserve_nulls(wire):
    message = AirbyteMessageSerializer.load(wire)
    assert json.loads(message_to_json(message)) == wire


@pytest.mark.parametrize("mode", ["append", "overwrite"])
@pytest.mark.parametrize("kind", ["LEGACY", "STREAM", "GLOBAL"])
def test_platform_checkpoint_envelope_survives_parse_write_and_spool(mode, kind):
    payload = {
        "LEGACY": {"data": {"cursor": None}},
        "STREAM": {"stream": {"stream_descriptor": {"name": "items"}, "stream_state": {"cursor": None}}},
        "GLOBAL": {"global": {"shared_state": {"cursor": None}, "stream_states": []}},
    }[kind]
    wire = {"type": "STATE", "state": {"type": kind, "id": 42, "future_field": {"value": None}, **payload}}
    inputs = DestinationRedisAdbc()._parse_input_stream(io.StringIO(json.dumps(wire) + "\n"))
    w, backend = writer()
    acknowledged = list(w.write(catalog(mode), inputs))
    assert [json.loads(message_to_json(message)) for message in acknowledged] == [wire]
    assert backend.cursor.adbc_ingest.called


@pytest.mark.parametrize(
    "line",
    [
        "not-json\n",
        '{"type":"UNKNOWN"}\n',
        '{"type":"RECORD"}\n',
        '{"type":"RECORD","record":{"stream":"items","data":{"bad":NaN},"emitted_at":1}}\n',
    ],
)
def test_malformed_input_is_never_silently_discarded(line):
    with pytest.raises(ConnectorError, match="Malformed"):
        list(DestinationRedisAdbc()._parse_input_stream(io.StringIO(line)))


@pytest.mark.parametrize(
    "payload", [{}, {"type": "STREAM", "data": {"cursor": 1}}, {"type": "GLOBAL", "data": {"cursor": 1}}]
)
def test_empty_and_mismatched_states_rejected(payload):
    with pytest.raises(ConnectorError):
        list(
            DestinationRedisAdbc()._parse_input_stream(
                io.StringIO(json.dumps({"type": "STATE", "state": payload}) + "\n")
            )
        )


def test_append_schema_mismatch_fails_before_any_write():
    w, b = writer(existing=True)
    b.schema = lambda connection, table: pa.schema([("id", pa.string())])
    with pytest.raises(ConnectorError, match="schema differs"):
        list(w.write(catalog(), [record(1)]))
    b.cursor.adbc_ingest.assert_not_called()
    b.cursor.execute.assert_not_called()


def test_multitable_promotion_failure_rolls_back_previously_promoted_table():
    w, b = writer(existing=True)
    b.existing[("airbyte", "other")] = Table("redis", "airbyte", "other")
    c = catalog("overwrite")
    c.streams += catalog("overwrite", name="other").streams
    failed = False

    def execute(sql):
        nonlocal failed
        if "airbyte_stage_" in sql and 'RENAME TO "other"' in sql and not failed:
            failed = True
            raise RuntimeError("injected second-table promotion failure")

    b.cursor.execute.side_effect = execute
    with pytest.raises(RuntimeError):
        list(w.write(c, [record(1), record(2, stream="other"), state()]))
    sql = [call.args[0] for call in b.cursor.execute.call_args_list]
    for name in ("items", "other"):
        assert any("airbyte_backup_" in command and f'RENAME TO "{name}"' in command for command in sql)
    assert not any(command.startswith("DROP TABLE ") and "airbyte_backup_" in command for command in sql)


def test_incomplete_recovery_retains_backup_and_fails_safely():
    w, b = writer(existing=True)

    def execute(sql):
        if 'RENAME TO "items"' in sql:
            raise RuntimeError("password=secret")

    b.cursor.execute.side_effect = execute
    with pytest.raises(ConnectorError, match="backup") as exc:
        list(w.write(catalog("overwrite"), [record(1), state()]))
    assert "secret" not in str(exc.value)
    assert not any(
        c.args[0].startswith("DROP TABLE ") and "airbyte_backup_" in c.args[0] for c in b.cursor.execute.call_args_list
    )


def test_source_error_trace_prevents_overwrite_publication():
    w, b = writer(existing=True)
    error = AirbyteMessageSerializer.load(
        {"type": "TRACE", "trace": {"type": "ERROR", "emitted_at": 1, "error": {"message": "source-secret"}}}
    )
    with pytest.raises(ConnectorError, match="source reported") as exc:
        list(w.write(catalog("overwrite"), [record(1), state(), error]))
    assert "source-secret" not in str(exc.value)
    assert not any("ALTER TABLE" in c.args[0] for c in b.cursor.execute.call_args_list)


def test_destination_cli_spec():
    result = subprocess.run([sys.executable, "-m", "destination_redis_adbc", "spec"], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    spec = next(json.loads(line)["spec"] for line in result.stdout.splitlines() if json.loads(line)["type"] == "SPEC")
    assert spec["supported_destination_sync_modes"] == ["append", "overwrite"]
