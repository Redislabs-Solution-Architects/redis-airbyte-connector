import json
import uuid
from contextlib import closing
from importlib.resources import files

import pyarrow as pa
from airbyte_cdk.destinations import Destination
from airbyte_cdk.exception_handler import init_uncaught_exception_handler
from airbyte_cdk.models import (
    AirbyteConnectionStatus,
    ConnectorSpecificationSerializer,
    Status,
    Type,
)

from source_redis_adbc.backend import (
    AdbcBackend,
    ConnectorError,
    quote,
    validate_config,
)

from .protocol import load_message, message_to_json, validate_state
from .writer import RedisWriter, logger


class DestinationRedisAdbc(Destination):
    def __init__(self, backend_factory=AdbcBackend):
        self.backend_factory = backend_factory

    def spec(self, logger):
        return ConnectorSpecificationSerializer.load(
            json.loads(files("destination_redis_adbc").joinpath("spec.json").read_text())
        )

    def _validate(self, config):
        validate_config(config, self.spec(None).connectionSpecification)
        schema = config.get("schema", "airbyte")
        if schema.startswith("pg_") or schema in ("information_schema", "redis_foreign"):
            raise ConnectorError("Destination system schemas are unsupported.")

    def check(self, logger, config):
        try:
            self._validate(config)
            backend = self.backend_factory(config)
            backend.check()
            schema = config.get("schema", "airbyte")
            probe = "airbyte_check_" + uuid.uuid4().hex
            with backend.connection() as connection, closing(connection.cursor()) as cursor:
                cursor.execute(f"CREATE SCHEMA IF NOT EXISTS {quote(schema)}")
                try:
                    count = cursor.adbc_ingest(
                        probe, pa.record_batch([[1]], names=["probe"]), mode="create", db_schema_name=schema
                    )
                    if count != 1:
                        raise ConnectorError("Destination write check did not confirm its probe row.")
                    with backend.query(connection, f"SELECT probe FROM {quote(schema)}.{quote(probe)}") as reader:
                        if sum(batch.num_rows for batch in reader) != 1:
                            raise ConnectorError("Destination write check could not read its probe row.")
                finally:
                    cursor.execute(f"DROP TABLE IF EXISTS {quote(schema)}.{quote(probe)}")
            return AirbyteConnectionStatus(status=Status.SUCCEEDED)
        except ConnectorError as error:
            return AirbyteConnectionStatus(status=Status.FAILED, message=str(error))

    def write(self, config, configured_catalog, input_messages):
        self._validate(config)
        yield from RedisWriter(config, self.backend_factory).write(configured_catalog, input_messages)

    def _parse_input_stream(self, input_stream):
        while True:
            line = input_stream.readline(16 * 1024 * 1024 + 1)
            if not line:
                return
            try:
                if len(line.encode("utf-8")) > 16 * 1024 * 1024:
                    raise ValueError("oversized input")
                data = json.loads(
                    line, parse_constant=lambda value: (_ for _ in ()).throw(ValueError("non-finite JSON"))
                )
                message = load_message(data)
                if message.type == Type.RECORD and message.record is None:
                    raise ValueError("record payload is required")
                if message.type == Type.STATE and message.state is None:
                    raise ValueError("state payload is required")
                if message.type == Type.STATE:
                    validate_state(message.state)
            except Exception:
                raise ConnectorError("Malformed or oversized Airbyte input; no line was silently discarded.") from None
            yield message

    def run(self, args):
        init_uncaught_exception_handler(logger)
        messages = self.run_cmd(self.parse_args(args))
        try:
            for message in messages:
                print(message_to_json(message), flush=True)
        finally:
            messages.close()
