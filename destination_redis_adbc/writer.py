"""Autocommit writes with bounded buffering and staged full-refresh promotion."""

import json
import logging
import tempfile
import uuid
from contextlib import closing, suppress
from dataclasses import dataclass, field

import pyarrow as pa
from airbyte_cdk.models import DestinationSyncMode, Type

from source_redis_adbc.backend import AdbcBackend, ConnectorError, quote

from .conversion import RecordConverter
from .protocol import load_message, validate_state

logger = logging.getLogger("airbyte.destination_redis_adbc")


@dataclass
class Plan:
    key: tuple
    namespace: str
    name: str
    converter: RecordConverter
    overwrite: bool
    work_name: str
    backup_name: str
    existed: bool = False
    prepared: bool = False
    promoted: bool = False
    backup_exists: bool = False
    rows: list = field(default_factory=list)

    def target(self, name=None):
        return f"{quote(self.namespace)}.{quote(name or self.name)}"


def plans_for(config, catalog):
    plans = {}
    targets = set()
    for configured in catalog.streams:
        stream = configured.stream
        key = (stream.namespace or None, stream.name)
        namespace = (
            stream.namespace
            if config.get("use_stream_namespace") and stream.namespace
            else config.get("schema", "airbyte")
        )
        if not stream.name or namespace.startswith("pg_") or namespace in ("information_schema", "redis_foreign"):
            raise ConnectorError("Destination system schemas and empty stream names are unsupported.")
        if key in plans or (namespace, stream.name) in targets:
            raise ConnectorError(
                "Duplicate destination stream or target; use separate namespaces to prevent collisions."
            )
        if configured.destination_sync_mode not in (DestinationSyncMode.append, DestinationSyncMode.overwrite):
            raise ConnectorError(
                "Only Append and Overwrite destination modes are supported; deduplication and CDC are unsupported."
            )
        overwrite = configured.destination_sync_mode == DestinationSyncMode.overwrite
        token = uuid.uuid4().hex
        plans[key] = Plan(
            key,
            namespace,
            stream.name,
            RecordConverter(stream.json_schema),
            overwrite,
            f"airbyte_stage_{token}" if overwrite else stream.name,
            f"airbyte_backup_{token}",
        )
        targets.add((namespace, stream.name))
    return plans


class RedisWriter:
    def __init__(self, config, backend_factory=AdbcBackend):
        self.config = config
        self.backend_factory = backend_factory

    def write(self, catalog, messages):
        plans = plans_for(self.config, catalog)
        backend = self.backend_factory({**self.config, "schemas": sorted({p.namespace for p in plans.values()})})
        deferred = any(p.overwrite for p in plans.values())
        buffered_rows = buffered_bytes = state_bytes = 0
        with (
            backend.connection() as connection,
            closing(connection.cursor()) as cursor,
            tempfile.TemporaryFile(mode="w+t") as states,
        ):
            try:
                existing = backend.tables(connection)
                # Fail mismatched append schemas before creating any tables.
                for plan in plans.values():
                    key = (plan.namespace, plan.name)
                    plan.existed = key in existing
                    if plan.existed and not plan.overwrite:
                        actual = backend.schema(connection, existing[key])
                        expected = plan.converter.arrow_schema
                        if {f.name: f.type for f in actual} != {f.name: f.type for f in expected}:
                            raise ConnectorError(
                                "Append target schema differs from the catalog; schema evolution requires an explicit migration."
                            )
                for plan in plans.values():
                    cursor.execute(f"CREATE SCHEMA IF NOT EXISTS {quote(plan.namespace)}")
                    # Mark before ingest so a failed create still cleans its staging table.
                    plan.prepared = True
                    cursor.adbc_ingest(
                        plan.work_name,
                        pa.RecordBatch.from_pylist([], schema=plan.converter.arrow_schema),
                        mode="create" if plan.overwrite else "create_append",
                        db_schema_name=plan.namespace,
                    )

                def flush():
                    nonlocal buffered_rows, buffered_bytes
                    for plan in plans.values():
                        if plan.rows:
                            batch = pa.RecordBatch.from_pylist(plan.rows, schema=plan.converter.arrow_schema)
                            count = cursor.adbc_ingest(
                                plan.work_name, batch, mode="append", db_schema_name=plan.namespace
                            )
                            if count != batch.num_rows:
                                raise ConnectorError(
                                    "Redis bulk ingest did not confirm the complete batch; state was not acknowledged."
                                )
                            plan.rows.clear()
                    buffered_rows = buffered_bytes = 0

                for message in messages:
                    if message.type == Type.RECORD:
                        record = message.record
                        key = (record.namespace or None, record.stream)
                        if key not in plans:
                            raise ConnectorError("Received a record for an unconfigured stream.")
                        try:
                            size = len(json.dumps(record.data, ensure_ascii=False, allow_nan=False).encode("utf-8"))
                        except Exception:
                            raise ConnectorError("A destination record is not valid finite JSON.") from None
                        if size > self.config.get("max_record_bytes", 10 * 1024 * 1024):
                            raise ConnectorError("A destination record exceeds max_record_bytes.")
                        if buffered_rows and buffered_bytes + size > self.config.get("max_batch_bytes", 1024 * 1024):
                            flush()
                        plans[key].rows.append(plans[key].converter.record(record.data))
                        buffered_rows += 1
                        buffered_bytes += size
                        if buffered_rows >= self.config.get("batch_size", 1024) or buffered_bytes >= self.config.get(
                            "max_batch_bytes", 1024 * 1024
                        ):
                            flush()
                    elif message.type == Type.STATE:
                        validate_state(message.state)
                        flush()
                        if deferred:
                            from .protocol import message_to_json

                            wire = message_to_json(message) + "\n"
                            state_bytes += len(wire.encode("utf-8"))
                            if state_bytes > self.config.get("max_state_bytes", 16 * 1024 * 1024):
                                raise ConnectorError(
                                    "Deferred Overwrite state exceeds max_state_bytes; no state was acknowledged."
                                )
                            states.write(wire)
                        else:
                            yield message
                    elif message.type == Type.TRACE and message.trace and message.trace.type.value == "ERROR":
                        raise ConnectorError(
                            "The source reported an error; the destination will not finalize Overwrite."
                        )
                    elif (
                        message.type == Type.TRACE and message.trace
                        and message.trace.type.value == "STREAM_STATUS"
                        and message.trace.stream_status
                        and message.trace.stream_status.status.value == "INCOMPLETE"
                    ):
                        raise ConnectorError(
                            "The source reported an incomplete stream; the destination will not finalize Overwrite."
                        )
                    elif message.type not in (Type.LOG, Type.TRACE):
                        raise ConnectorError("Unsupported input message type for the Redis destination.")
                flush()
                self._promote(cursor, list(plans.values()))
                if deferred:
                    states.seek(0)
                    for line in states:
                        yield load_message(json.loads(line))
            finally:
                # Best effort cancellation/cleanup must not replace the primary error.
                with suppress(Exception):
                    cursor.adbc_cancel()
                for plan in plans.values():
                    if plan.overwrite and plan.prepared and not plan.promoted:
                        with suppress(Exception):
                            cursor.execute(f"DROP TABLE IF EXISTS {plan.target(plan.work_name)}")

    @staticmethod
    def _promote(cursor, plans):
        overwrites = [p for p in plans if p.overwrite]
        try:
            for plan in overwrites:
                if plan.existed:
                    cursor.execute(f"ALTER TABLE {plan.target()} RENAME TO {quote(plan.backup_name)}")
                    plan.backup_exists = True
                cursor.execute(f"ALTER TABLE {plan.target(plan.work_name)} RENAME TO {quote(plan.name)}")
                plan.promoted = True
        except BaseException:
            recovery_failed = False
            for plan in reversed(overwrites):
                try:
                    if plan.promoted:
                        cursor.execute(f"ALTER TABLE {plan.target()} RENAME TO {quote(plan.work_name)}")
                        plan.promoted = False
                    if plan.backup_exists:
                        cursor.execute(f"ALTER TABLE {plan.target(plan.backup_name)} RENAME TO {quote(plan.name)}")
                        plan.backup_exists = False
                except BaseException:
                    recovery_failed = True
            if recovery_failed:
                raise ConnectorError(
                    "Overwrite recovery was incomplete. Previous data was retained in airbyte_backup_* tables; inspect the destination schema before retrying."
                ) from None
            raise
        for plan in overwrites:
            if plan.backup_exists:
                try:
                    cursor.execute(f"DROP TABLE {plan.target(plan.backup_name)}")
                    plan.backup_exists = False
                except Exception:
                    logger.warning(
                        "Overwrite succeeded; an airbyte_backup_* table requires cleanup in the destination schema."
                    )
