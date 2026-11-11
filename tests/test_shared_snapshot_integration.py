"""Full connector reads against the released native shared-view API."""

from collections import Counter
from contextlib import closing, contextmanager
import json
import time
import uuid

import pytest
from airbyte_cdk.models import ConfiguredAirbyteCatalogSerializer, Status

from scripts.integration_config import enterprise_config
from source_redis_adbc.backend import AdbcBackend, ConnectorError, connect, quote
from source_redis_adbc.source import SourceRedisAdbc


pytestmark = pytest.mark.integration


@pytest.fixture
def snapshot_tables():
    config = enterprise_config()
    names = ["airbyte_shared_" + uuid.uuid4().hex[:12] for _ in range(3)]
    first, second, view = map(quote, names)
    with closing(connect(config)) as writer, closing(writer.cursor()) as cursor:
        created = []
        try:
            for name in (first, second):
                cursor.execute(f"CREATE TABLE {name} (id BIGINT, label VARCHAR)")
                created.append(("TABLE", name))
                cursor.execute(f"INSERT INTO {name} VALUES (9007199254740993, 'old'), (2, NULL), (2, NULL), (3, 'three'), (4, 'four')")
            cursor.execute(f"CREATE VIEW {view} AS SELECT id, label FROM {second}")
            created.append(("VIEW", view))
            yield config, names, cursor
        finally:
            for kind, name in reversed(created):
                cursor.execute(f"DROP {kind} IF EXISTS {name}")


def catalog_for(source, config, names):
    streams = {stream.name: stream for stream in source.discover(None, config).streams}
    return ConfiguredAirbyteCatalogSerializer.load({"streams": [
        {"stream": {"name": name, "namespace": streams[name].namespace,
                    "json_schema": streams[name].json_schema, "supported_sync_modes": ["full_refresh"]},
         "sync_mode": "full_refresh", "destination_sync_mode": "overwrite"}
        for name in names
    ]})


class ObservedBackend(AdbcBackend):
    """Check release before the connection closes, without changing its API."""

    @contextmanager
    def read_snapshot(self, connection):
        try:
            with super().read_snapshot(connection):
                yield
        finally:
            status = json.loads(connection.adbc_connection.get_option("adbc.redis.read_snapshot_status"))
            assert status == {"state": "unbound"}
            self.released = True


def test_shared_snapshot_keeps_metadata_views_and_all_table_values(snapshot_tables):
    config, names, writer = snapshot_tables
    config = {**config, "read_consistency": "shared_snapshot"}
    with closing(connect(config)) as connection:
        assert connection.adbc_get_info()["driver_version"] == "v0.0.24"
    source = SourceRedisAdbc()
    assert source.check(None, config).status == Status.SUCCEEDED
    catalog = catalog_for(source, config, names)
    first, second, view = map(quote, names)

    class PublishingBackend(ObservedBackend):
        def tables(self, connection):
            # The source must already be bound before read-time rediscovery,
            # not merely before the first SELECT or in a prior discover job.
            status = json.loads(connection.adbc_connection.get_option("adbc.redis.read_snapshot_status"))
            assert status["state"] == "active"
            writer.execute(f"UPDATE {first} SET label = 'new'")
            writer.execute(f"ALTER TABLE {second} ADD COLUMN extra VARCHAR")
            writer.execute(f"INSERT INTO {second} (id, label, extra) VALUES (5, 'new', 'added')")
            writer.execute(f"DROP VIEW {view}")
            return super().tables(connection)

    backend = PublishingBackend(config)
    source = SourceRedisAdbc(lambda _: backend)
    messages = list(source.read(None, config, catalog))
    expected = Counter([
        ('9007199254740993', 'old'), ('2', None), ('2', None), ('3', 'three'), ('4', 'four'),
    ])
    for name in names:
        rows = [message.record.data for message in messages if message.record.stream == name]
        assert all(set(row) == {"id", "label"} for row in rows)
        assert Counter((row["id"], row["label"]) for row in rows) == expected
    assert backend.released
    current = {s.name: s for s in SourceRedisAdbc().discover(None, config).streams}
    assert names[2] not in current
    assert "extra" in current[names[1]].json_schema["properties"]
    writer.execute(f"SELECT label FROM {first}")
    assert set(writer.fetchall()) == {('new',)}


def test_shared_snapshot_generator_close_releases_binding(snapshot_tables):
    config, names, _ = snapshot_tables
    config = {**config, "read_consistency": "shared_snapshot"}
    catalog = catalog_for(SourceRedisAdbc(), config, names[:2])
    backend = ObservedBackend(config)
    with closing(SourceRedisAdbc(lambda _: backend).read(None, config, catalog)) as records:
        next(records)
    assert backend.released


def test_shared_snapshot_expiry_fails_after_partial_emission(snapshot_tables):
    config, names, _ = snapshot_tables
    config = {**config, "read_consistency": "shared_snapshot", "snapshot_retention_ms": 120000}
    catalog = catalog_for(SourceRedisAdbc(), config, names[:2])
    backend = ObservedBackend(config)
    with closing(SourceRedisAdbc(lambda _: backend).read(None, config, catalog)) as records:
        next(records)
        # Use the public minimum lifetime, rather than changing server time or
        # mutating the driver's private lease/control keys to simulate expiry.
        time.sleep(121)
        with pytest.raises(ConnectorError):
            list(records)
    assert backend.released
