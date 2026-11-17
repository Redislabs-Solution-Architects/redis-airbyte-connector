"""Full Airbyte source lifecycle through the actual ADBC Flight SQL client."""
from collections import Counter
from contextlib import closing, contextmanager
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest
from airbyte_cdk.models import ConfiguredAirbyteCatalogSerializer, Status

from source_redis_adbc.backend import AdbcBackend, connect
from source_redis_adbc.source import SourceRedisAdbc

pytestmark = pytest.mark.integration
SESSION = "adbc.flight.sql.session.option."


@pytest.fixture
def flight_tables():
    uri = os.environ.get("FLIGHT_URI", "")
    if not uri.startswith("grpc+tls://"):
        pytest.fail("Flight qualification requires a disposable authenticated TLS gateway")
    options = {"username": os.environ["FLIGHT_USERNAME"], "password": os.environ["FLIGHT_PASSWORD"]}
    if os.environ.get("FLIGHT_TLS_ROOT_CERTS"):
        options["adbc.flight.sql.client_option.tls_root_certs"] = Path(os.environ["FLIGHT_TLS_ROOT_CERTS"]).read_text()
    config = {"uri": uri, "connection_type": "flight_sql", "database_options": options, "read_consistency": "shared_snapshot", "batch_size": 2}
    names = ["it_airbyte_flight_" + uuid4().hex[:12] for _ in range(3)]
    with closing(connect({**config, "read_consistency": "best_effort"})) as writer, closing(writer.cursor()) as cur:
        try:
            for name in names[:2]:
                cur.execute(f"CREATE TABLE {name}(id BIGINT,label VARCHAR)")
                cur.execute(f"INSERT INTO {name} VALUES(9007199254740993,'雪'),(2,NULL),(2,NULL),(3,'old')")
            cur.execute(f"CREATE VIEW {names[2]} AS SELECT id,label FROM {names[1]}")
            yield config, names, cur
        finally:
            cur.execute(f"DROP VIEW IF EXISTS {names[2]}")
            for name in names[:2]:
                cur.execute(f"DROP TABLE IF EXISTS {name}")


def catalog(config, names):
    source = SourceRedisAdbc()
    assert source.check(None, config).status == Status.SUCCEEDED
    streams = {stream.name: stream for stream in source.discover(None, config).streams}
    return ConfiguredAirbyteCatalogSerializer.load({"streams": [
        {"stream": {"name": name, "namespace": streams[name].namespace,
                    "json_schema": streams[name].json_schema, "supported_sync_modes": ["full_refresh"]},
         "sync_mode": "full_refresh", "destination_sync_mode": "overwrite"}
        for name in names
    ]})


class ObservedBackend(AdbcBackend):
    @contextmanager
    def read_snapshot(self, connection):
        try:
            with super().read_snapshot(connection):
                yield
        finally:
            state = json.loads(connection.adbc_connection.get_option(SESSION + "redis.read_snapshot_status"))
            assert state["state"] == "released"
            self.released = True


def test_flight_source_check_discover_and_multitable_snapshot(flight_tables):
    config, names, writer = flight_tables
    configured = catalog(config, names)

    class PublishingBackend(ObservedBackend):
        def tables(self, connection):
            state = json.loads(connection.adbc_connection.get_option(SESSION + "redis.read_snapshot_status"))
            assert state["state"] == "active"  # binding precedes read-time discovery
            writer.execute(f"UPDATE {names[0]} SET label='new'")
            writer.execute(f"ALTER TABLE {names[1]} ADD COLUMN added VARCHAR")
            writer.execute(f"TRUNCATE TABLE {names[1]}")
            writer.execute(f"DROP VIEW {names[2]}")
            return super().tables(connection)

    backend = PublishingBackend(config)
    records = list(SourceRedisAdbc(lambda _: backend).read(None, config, configured))
    expected = Counter([("9007199254740993", "雪"), ("2", None), ("2", None), ("3", "old")])
    for name in names:
        rows = [message.record.data for message in records if message.record.stream == name]
        assert all(set(row) == {"id", "label"} for row in rows)
        assert Counter((row["id"], row["label"]) for row in rows) == expected
    assert backend.released
    current = {stream.name: stream for stream in SourceRedisAdbc().discover(None, config).streams}
    assert names[2] not in current
    assert "added" in current[names[1]].json_schema["properties"]


def test_flight_source_generator_close_releases_snapshot(flight_tables):
    config, names, _ = flight_tables
    configured = catalog(config, names[:2])
    backend = ObservedBackend(config)
    records = SourceRedisAdbc(lambda _: backend).read(None, config, configured)
    next(records)
    records.close()
    assert backend.released
