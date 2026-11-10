from contextlib import closing
from unittest.mock import Mock

import pyarrow as pa
import pytest

from source_redis_adbc.backend import AdbcBackend, ConnectorError, connect, quote, validate_config
from source_redis_adbc.conversion import schema_to_json
from source_redis_adbc.source import SourceRedisAdbc


CONFIG = {"uri": "redis://user:secret@localhost:6379/0"}
SCHEMA = pa.schema([pa.field("id", pa.int64()), pa.field("label", pa.string())])


class Reader:
    def __init__(self, batches, schema=SCHEMA, late_error=None):
        self.schema = schema
        self.batches = list(batches)
        self.late_error = late_error
        self.consumed = 0
        self.closed = False

    def __iter__(self):
        for batch in self.batches:
            self.consumed += 1
            yield batch
        if self.late_error:
            raise self.late_error

    def close(self):
        self.closed = True


def metadata(catalog="redis", namespace="public", name="items"):
    return {"catalog_name": catalog, "catalog_db_schemas": [{"db_schema_name": namespace,
        "db_schema_tables": [{"table_name": name, "table_type": "TABLE"}]}]}


def backend(reader, rows=None):
    connection = Mock()
    cursor = connection.cursor.return_value
    cursor.fetch_record_batch.return_value = reader
    connection.adbc_get_objects.side_effect = lambda **kw: Reader([pa.RecordBatch.from_pylist(rows or [metadata()])])
    connection.adbc_get_table_schema.return_value = SCHEMA
    factory = Mock(return_value=connection)
    return AdbcBackend(CONFIG, factory), connection, cursor


def selection(schema=SCHEMA):
    return [("public", "items", schema_to_json(schema)["properties"])]


def test_multibatch_is_lazy_and_closes():
    batches = [pa.record_batch([[n], [str(n)]], schema=SCHEMA) for n in range(3)]
    reader = Reader(batches)
    source, connection, cursor = backend(reader)
    gen = source.read(selection())
    assert next(gen)[1] == {"id": "0", "label": "0"}
    assert reader.consumed == 1  # backpressure: later batches are not requested
    assert [record for _, record in gen] == [{"id": "1", "label": "1"}, {"id": "2", "label": "2"}]
    assert reader.closed
    cursor.close.assert_called_once()
    connection.close.assert_called_once()
    cursor.adbc_cancel.assert_not_called()


def test_early_consumer_cancellation_closes_and_cancels():
    reader = Reader([pa.record_batch([[1], ["a"]], schema=SCHEMA)])
    source, connection, cursor = backend(reader)
    with closing(source.read(selection())) as gen:
        next(gen)
    assert reader.closed
    cursor.adbc_cancel.assert_called_once()
    cursor.close.assert_called_once()
    connection.close.assert_called_once()


def test_late_error_sanitized_after_partial_output():
    reader = Reader([pa.record_batch([[1], ["a"]], schema=SCHEMA)], late_error=RuntimeError(CONFIG["uri"]))
    source, connection, cursor = backend(reader)
    gen = source.read(selection())
    next(gen)
    with pytest.raises(ConnectorError) as exc:
        next(gen)
    assert "secret" not in str(exc.value)
    assert exc.value.__suppress_context__
    assert reader.closed
    cursor.adbc_cancel.assert_called_once()
    connection.close.assert_called_once()


def test_check_consumes_late_error():
    reader = Reader([], late_error=RuntimeError("password=secret"))
    source, connection, cursor = backend(reader)
    with pytest.raises(ConnectorError):
        source.check()
    assert reader.closed
    connection.close.assert_called_once()


def test_empty_result_has_no_records_and_closes():
    reader = Reader([])
    source, connection, _ = backend(reader)
    assert list(source.read(selection())) == []
    assert reader.closed
    connection.close.assert_called_once()


def test_selected_field_and_quoted_identifiers():
    projected = pa.schema([SCHEMA.field("id")])
    reader = Reader([pa.record_batch([[1]], schema=projected)], schema=projected)
    source, _, cursor = backend(reader, [metadata(name='items"; DROP TABLE t;--')])
    selected = [("public", 'items"; DROP TABLE t;--', schema_to_json(projected)["properties"])]
    assert list(source.read(selected))[0][1] == {"id": "1"}
    cursor.execute.assert_called_once_with('SELECT "id" FROM "redis"."public"."items""; DROP TABLE t;--"')
    assert quote('a"b') == '"a""b"'


@pytest.mark.parametrize("selections", [
    [("public", "missing", {"id": {"type": "string"}})],
    [("public", "items", {"gone": {"type": "string"}})],
    [("public", "items", {"id": {"type": "integer"}})],
    [("public", "items", {})],
    selection() * 2,
])
def test_invalid_selection_fails_before_query(selections):
    source, connection, cursor = backend(Reader([]))
    with pytest.raises(ConnectorError):
        list(source.read(selections))
    cursor.execute.assert_not_called()
    connection.close.assert_called_once()


def test_ambiguous_catalog_fails():
    source, _, _ = backend(Reader([]), [metadata("one"), metadata("two")])
    with pytest.raises(ConnectorError, match="Ambiguous"):
        source.discover()
    source.config = {**CONFIG, "catalog": "two"}
    assert source.discover()[0][0].catalog == "two"


def test_discovery_respects_exact_schema_allowlist():
    source, _, _ = backend(Reader([]), [metadata(namespace="public"), metadata(namespace="private")])
    assert [table.namespace for table, _ in source.discover()] == ["public"]


def test_connection_failures_are_safe():
    factory = Mock(side_effect=RuntimeError(CONFIG["uri"]))
    source = AdbcBackend(CONFIG, factory)
    with pytest.raises(ConnectorError) as exc:
        source.check()
    assert "secret" not in str(exc.value)


def test_shared_snapshot_covers_metadata_and_two_queries_until_cleanup():
    reader = Reader([pa.record_batch([[1], ["a"]], schema=SCHEMA)])
    source, connection, cursor = backend(reader, [metadata(), metadata(name="other")])
    source.config = {**CONFIG, "read_consistency": "shared_snapshot", "snapshot_retention_ms": 120000}
    events = []
    options = connection.adbc_connection
    options.set_options.side_effect = lambda **kw: events.append(kw)
    original_metadata = connection.adbc_get_objects.side_effect
    def objects(**kw):
        assert events[-1] == {"adbc.redis.read_snapshot_action": "acquire"}
        return original_metadata(**kw)
    connection.adbc_get_objects.side_effect = objects
    connection.adbc_get_table_schema.side_effect = lambda *a, **kw: (
        events[-1] == {"adbc.redis.read_snapshot_action": "acquire"} and SCHEMA
    )
    selections = selection() + [("public", "other", schema_to_json(SCHEMA)["properties"])]
    records = source.read(selections)
    assert next(records)[0].name == "items"
    assert events == [
        {"adbc.redis.read_snapshot_retention_ms": 120000},
        {"adbc.redis.read_snapshot_action": "acquire"},
    ]
    assert [table.name for table, _ in records] == ["other"]
    assert events[-1] == {"adbc.redis.read_snapshot_action": "release"}
    assert cursor.execute.call_count == 2
    connection.close.assert_called_once()


@pytest.mark.parametrize("action", ["check", "discover", "read"])
def test_shared_snapshot_acquisition_failure_is_terminal_and_safe(action):
    source, connection, cursor = backend(Reader([]))
    source.config = {**CONFIG, "read_consistency": "shared_snapshot"}
    def options(**kw):
        if kw.get("adbc.redis.read_snapshot_action") == "acquire":
            raise RuntimeError(CONFIG["uri"])
    connection.adbc_connection.set_options.side_effect = options
    with pytest.raises(ConnectorError, match="Could not acquire") as error:
        if action == "read":
            list(source.read(selection()))
        else:
            getattr(source, action)()
    assert "secret" not in str(error.value)
    connection.adbc_get_objects.assert_not_called()
    cursor.execute.assert_not_called()
    connection.adbc_connection.set_options.assert_any_call(**{"adbc.redis.read_snapshot_action": "release"})
    source.connect_fn.assert_called_once()
    connection.close.assert_called_once()


@pytest.mark.parametrize("failure", ["cancel", "expired", "schema"])
def test_shared_snapshot_failure_releases_after_reader_cleanup_without_retry(failure):
    reader = Reader([pa.record_batch([[1], ["a"]], schema=SCHEMA)],
                    late_error=RuntimeError("expired " + CONFIG["uri"]) if failure == "expired" else None)
    source, connection, cursor = backend(reader)
    source.config = {**CONFIG, "read_consistency": "shared_snapshot"}
    if failure == "schema":
        reader.schema = pa.schema([pa.field("changed", pa.int64())])
    def options(**kw):
        if kw.get("adbc.redis.read_snapshot_action") == "release":
            assert reader.closed
            assert cursor.close.called
    connection.adbc_connection.set_options.side_effect = options
    with closing(source.read(selection())) as records:
        if failure == "schema":
            with pytest.raises(ConnectorError, match="Query schema changed"):
                next(records)
        else:
            next(records)
            if failure == "expired":
                with pytest.raises(ConnectorError) as error:
                    next(records)
                assert "secret" not in str(error.value)
    connection.adbc_connection.set_options.assert_any_call(**{"adbc.redis.read_snapshot_action": "release"})
    source.connect_fn.assert_called_once()
    connection.close.assert_called_once()


def test_snapshot_release_error_does_not_hide_primary_error():
    source, connection, _ = backend(Reader([]))
    source.config = {**CONFIG, "read_consistency": "shared_snapshot"}
    def options(**kw):
        if kw.get("adbc.redis.read_snapshot_action") == "release":
            raise RuntimeError(CONFIG["uri"])
    connection.adbc_connection.set_options.side_effect = options
    with pytest.raises(ConnectorError, match="Selected stream"):
        list(source.read([("public", "missing", {})]))
    connection.close.assert_called_once()
    with pytest.raises(ConnectorError) as error:
        source.discover()
    assert "secret" not in str(error.value)


def test_connection_modes_share_generic_backend(monkeypatch):
    manager = Mock()
    monkeypatch.setattr("source_redis_adbc.backend.dbapi.connect", manager)
    monkeypatch.setenv("REDIS_ADBC_LIBRARY", "/driver.so")
    connect(CONFIG)
    args = manager.call_args.kwargs
    assert args["autocommit"] is True
    assert args["entrypoint"] == "AdbcDriverRedisInit"
    assert args["db_kwargs"]["adbc.redis.stream_results"] == "true"
    import adbc_driver_flightsql.dbapi as flight
    gateway = Mock()
    monkeypatch.setattr(flight, "connect", gateway)
    connect({"uri": "grpc+tls://localhost:1234", "connection_type": "flight_sql", "database_options": {"username": "test"}})
    gateway.assert_called_once_with("grpc+tls://localhost:1234", db_kwargs={"username": "test"}, autocommit=True)


def test_default_connection_uses_bundled_native_driver(monkeypatch):
    from pathlib import Path

    monkeypatch.delenv("REDIS_ADBC_LIBRARY", raising=False)
    monkeypatch.setattr(Path, "is_file", lambda path: path.name == "libadbc_driver_redis.so")
    manager = Mock()
    monkeypatch.setattr("source_redis_adbc.backend.dbapi.connect", manager)
    connect(CONFIG)
    args = manager.call_args.kwargs
    assert args["driver"].endswith("source_redis_adbc/libadbc_driver_redis.so")
    assert args["autocommit"] is True
    assert args["db_kwargs"]["uri"] == CONFIG["uri"]
    assert args["db_kwargs"]["adbc.redis.stream_results"] == "true"


def test_missing_native_library_has_actionable_error(monkeypatch):
    from pathlib import Path

    monkeypatch.delenv("REDIS_ADBC_LIBRARY", raising=False)
    monkeypatch.setattr(Path, "is_file", lambda path: False)
    with pytest.raises(ConnectorError, match="Install a supported platform wheel"):
        connect(CONFIG)


@pytest.mark.parametrize("config", [
    {}, {"uri": "http://localhost"}, {**CONFIG, "batch_size": 0},
    {**CONFIG, "schemas": []}, {**CONFIG, "database_options": {"uri": "redis://other"}},
    {**CONFIG, "connection_type": "flight_sql"}, {**CONFIG, "database_options": {"option": 123}},
    {**CONFIG, "snapshot_retention_ms": 119999}, {**CONFIG, "snapshot_retention_ms": 3600001},
    {**CONFIG, "database_options": {"adbc.redis.read_snapshot_action": "release"}},
    {**CONFIG, "database_options": {"adbc.redis.read_snapshot_retention_ms": "120000"}},
])
def test_invalid_config_is_safe(config):
    with pytest.raises(ConnectorError) as exc:
        validate_config(config, SourceRedisAdbc().spec(None).connectionSpecification)
    assert "secret" not in str(exc.value)


def test_inline_ca_lifetime_and_cleanup(monkeypatch):
    from pathlib import Path
    from unittest.mock import Mock
    import source_redis_adbc.backend as backend

    native = Mock()
    paths = []
    def opened(config):
        path = Path(config['database_options']['adbc.redis.tls_ca_file'])
        assert path.read_text() == 'test CA'
        assert path.stat().st_mode & 0o777 == 0o600
        paths.append(path)
        return native
    monkeypatch.setattr(backend, '_connect', opened)
    with backend.connect({'uri': 'rediss://example', 'tls_ca_certificate': 'test CA'}) as connection:
        assert connection.cursor() is native.cursor.return_value
        assert paths[0].exists()
    native.close.assert_called_once()
    assert not paths[0].exists()


def test_inline_ca_cleanup_on_open_failure(monkeypatch):
    from pathlib import Path
    import source_redis_adbc.backend as backend

    paths = []
    def failed(config):
        paths.append(Path(config['database_options']['adbc.redis.tls_ca_file']))
        raise RuntimeError('test connection failure')
    monkeypatch.setattr(backend, '_connect', failed)
    with pytest.raises(RuntimeError, match='test connection failure'):
        backend.connect({'uri': 'rediss://example', 'tls_ca_certificate': 'test CA'})
    assert not paths[0].exists()


@pytest.mark.parametrize('config', [
    {'uri': 'redis://example'},
    {'uri': 'rediss://example', 'database_options': {'adbc.redis.tls_ca_file': '/other'}},
    {'uri': 'grpc+tls://example', 'connection_type': 'flight_sql'},
])
def test_inline_ca_rejects_conflicting_modes(config):
    from source_redis_adbc.backend import connect
    with pytest.raises(ConnectorError, match='Inline Redis CA'):
        connect({**config, 'tls_ca_certificate': 'test CA'})
