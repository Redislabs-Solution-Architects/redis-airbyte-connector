from contextlib import closing
import uuid

import pytest
from airbyte_cdk.models import ConfiguredAirbyteCatalogSerializer, Status

from scripts.integration_config import enterprise_config, flight_config
from source_redis_adbc.backend import connect, quote
from source_redis_adbc.source import SourceRedisAdbc


@pytest.mark.integration
def test_redis_enterprise_flight_sql_server():
    # Seed through native ADBC and read through the gateway to prove it uses
    # the same Redis Enterprise database, rather than a reference SQLite server.
    native = enterprise_config()
    config = flight_config()
    name = "airbyte_flight_it_" + uuid.uuid4().hex[:12]
    with closing(connect(native)) as connection, closing(connection.cursor()) as cursor:
        cursor.execute(f'CREATE TABLE {quote(name)} (id BIGINT, label VARCHAR)')
        try:
            cursor.execute(f"INSERT INTO {quote(name)} VALUES (1, 'one'), (2, NULL)")
            source = SourceRedisAdbc()
            assert source.check(None, config).status == Status.SUCCEEDED
            stream = next(s for s in source.discover(None, config).streams if s.name == name)
            configured = ConfiguredAirbyteCatalogSerializer.load({"streams": [{"stream":
                {"name": stream.name, "namespace": stream.namespace,
                 "json_schema": stream.json_schema, "supported_sync_modes": ["full_refresh"]}, "sync_mode": "full_refresh", "destination_sync_mode": "overwrite"}]})
            rows = [message.record.data for message in source.read(None, config, configured)]
            assert sorted(rows, key=lambda row: row["id"]) == [
                {"id": "1", "label": "one"}, {"id": "2", "label": None},
            ]
        finally:
            cursor.execute(f'DROP TABLE {quote(name)}')
