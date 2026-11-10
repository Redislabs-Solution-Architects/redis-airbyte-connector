"""The extraction layer is independent of Airbyte and the particular ADBC driver."""

import os
from pathlib import Path
import tempfile
from contextlib import closing, contextmanager, suppress
from dataclasses import dataclass

import adbc_driver_manager.dbapi as dbapi
import jsonschema

from .conversion import ConversionError, batch_records, schema_to_json


class ConnectorError(RuntimeError):
    """Safe to show to users; never contains raw driver exception text."""


@dataclass(frozen=True)
class Table:
    catalog: str | None
    namespace: str | None
    name: str


def validate_config(config, spec):
    try:
        jsonschema.Draft7Validator(spec).validate(config)
    except jsonschema.ValidationError:
        raise ConnectorError("Invalid connector configuration; check the connection specification.") from None
    if any(not isinstance(value, str) for value in config.get("database_options", {}).values()):
        raise ConnectorError("Database option values must be strings.")
    mode = config.get("connection_type", "redis")
    schemes = ("redis://", "rediss://") if mode == "redis" else ("grpc://", "grpc+tls://", "grpc+tcp://")
    if not config["uri"].startswith(schemes):
        raise ConnectorError("URI scheme does not match the selected connection type.")
    forbidden = {"uri", "driver", "entrypoint", "adbc.connection.autocommit"}
    options = config.get("database_options", {})
    if forbidden.intersection(options) or any(key.startswith("adbc.redis.read_snapshot_") for key in options):
        raise ConnectorError("Database options cannot override connection, transaction or read snapshot settings.")


def connect(config):
    certificate = config.get("tls_ca_certificate")
    if not certificate:
        return _connect(config)
    options = dict(config.get("database_options", {}))
    if (config.get("connection_type", "redis") != "redis" or
            not config["uri"].startswith("rediss://") or "adbc.redis.tls_ca_file" in options):
        raise ConnectorError("Inline Redis CA certificates require rediss:// and cannot be combined with a CA file.")
    directory = tempfile.TemporaryDirectory(prefix="redis-airbyte-ca-")
    try:
        path = Path(directory.name) / "ca.pem"
        path.write_text(certificate)
        path.chmod(0o600)
        options["adbc.redis.tls_ca_file"] = str(path)
        connection = _connect({**config, "database_options": options})
    except BaseException:
        directory.cleanup()
        raise
    return _CertificateConnection(connection, directory)


class _CertificateConnection:
    """Keep the private CA file available until all native handles are closed."""

    def __init__(self, connection, directory):
        self.connection = connection
        self.directory = directory

    def __getattr__(self, name):
        return getattr(self.connection, name)

    def close(self):
        try:
            self.connection.close()
        finally:
            self.directory.cleanup()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def _connect(config):
    options = dict(config.get("database_options", {}))
    if config.get("connection_type", "redis") == "flight_sql":
        from adbc_driver_flightsql import dbapi as flight

        return flight.connect(config["uri"], db_kwargs=options, autocommit=True)
    options.update({
        "uri": config["uri"],
        "adbc.redis.stream_results": "true",
        "adbc.redis.stream_batch_rows": str(config.get("batch_size", 1024)),
    })
    return dbapi.connect(
        driver=driver_path(),
        entrypoint="AdbcDriverRedisInit", db_kwargs=options, autocommit=True,
    )


def driver_path():
    override = os.environ.get("REDIS_ADBC_LIBRARY")
    if override:
        return override
    for name in ("libadbc_driver_redis.so", "libadbc_driver_redis.dylib", "adbc_driver_redis.dll"):
        path = Path(__file__).with_name(name)
        if path.is_file():
            return str(path)
    raise ConnectorError(
        "Redis native library is missing. Install a supported platform wheel or set "
        "REDIS_ADBC_LIBRARY to a built Redis ADBC library."
    )


class AdbcBackend:
    def __init__(self, config, connect_fn=connect):
        self.config = config
        self.connect_fn = connect_fn

    @contextmanager
    def connection(self):
        try:
            if (self.config.get("read_consistency") == "shared_snapshot" and
                    self.config.get("connection_type", "redis") != "redis"):
                raise ConnectorError("Shared snapshots are unsupported for Flight SQL.")
            with closing(self.connect_fn(self.config)) as connection:
                with self.read_snapshot(connection):
                    yield connection
        except (ConnectorError, ConversionError):
            raise
        except Exception:
            # ADBC diagnostics can include credentials, option values and URIs.
            raise ConnectorError("ADBC operation failed. Verify connectivity, permissions, Search support and schema; driver diagnostics were withheld to protect credentials.") from None

    @contextmanager
    def read_snapshot(self, connection):
        if self.config.get("read_consistency") != "shared_snapshot":
            yield
            return
        options = connection.adbc_connection
        try:
            try:
                options.set_options(**{
                    "adbc.redis.read_snapshot_retention_ms": self.config.get("snapshot_retention_ms", 900000),
                })
                # Successful acquisition is the capability probe, including for
                # custom REDIS_ADBC_LIBRARY overrides. Never downgrade or retry.
                options.set_options(**{"adbc.redis.read_snapshot_action": "acquire"})
            except Exception:
                raise ConnectorError(
                    "Could not acquire the required shared snapshot. Use Redis ADBC v0.0.24 "
                    "or a compatible newer library, protocol-17 managed storage and sufficient "
                    "snapshot permissions/capacity. Driver diagnostics were withheld to protect credentials."
                ) from None
            yield
        except BaseException:
            # Close/failure must retain the original error, even if release also
            # fails. Connection close and server lease expiry remain fallbacks.
            with suppress(Exception):
                options.set_options(**{"adbc.redis.read_snapshot_action": "release"})
            raise
        else:
            options.set_options(**{"adbc.redis.read_snapshot_action": "release"})

    @contextmanager
    def query(self, connection, sql):
        with closing(connection.cursor()) as cursor:
            complete = False
            try:
                cursor.execute(sql)
                with closing(cursor.fetch_record_batch()) as reader:
                    yield reader
                    complete = True
            finally:
                if not complete:
                    with suppress(Exception):
                        cursor.adbc_cancel()

    def tables(self, connection):
        schemas = self.config.get("schemas", ["public"])
        wanted_catalog = self.config.get("catalog")
        result = {}
        with closing(connection.adbc_get_objects(depth="tables")) as reader:
            for batch in reader:
                for catalog in batch.to_pylist():
                    cat = catalog["catalog_name"]
                    if wanted_catalog is not None and cat != wanted_catalog:
                        continue
                    for schema in catalog["catalog_db_schemas"] or []:
                        namespace = schema["db_schema_name"]
                        if (namespace or "") not in schemas:
                            continue
                        for table in schema["db_schema_tables"] or []:
                            if table["table_type"].upper() not in ("TABLE", "BASE TABLE", "VIEW"):
                                continue
                            obj = Table(cat, namespace, table["table_name"])
                            key = (namespace, obj.name)
                            if key in result:
                                raise ConnectorError("Ambiguous stream names across catalogs; configure an exact catalog.")
                            result[key] = obj
        return result

    def schema(self, connection, table):
        return connection.adbc_get_table_schema(table.name, catalog_filter=table.catalog, db_schema_filter=table.namespace)

    def check(self):
        with self.connection() as connection:
            with self.query(connection, "SELECT 1") as reader:
                for _ in reader:
                    pass
            # Also exercise Search-backed metadata, rather than only SELECT 1.
            self.tables(connection)

    def discover(self):
        with self.connection() as connection:
            return [(table, schema_to_json(self.schema(connection, table))) for table in self.tables(connection).values()]

    def read(self, selections):
        with self.connection() as connection:
            tables = self.tables(connection)
            # Validate all selected streams before emitting any records.
            plans = []
            seen = set()
            for namespace, name, selected in selections:
                key = (namespace, name)
                if key in seen or key not in tables:
                    raise ConnectorError("Selected stream is missing or duplicated; rediscover the catalog.")
                seen.add(key)
                table = tables[key]
                schema = self.schema(connection, table)
                properties = schema_to_json(schema)["properties"]
                if not selected or any(field not in properties for field in selected):
                    raise ConnectorError("Selected fields are empty or missing; rediscover the catalog.")
                if any(selected[field] != properties[field] for field in selected):
                    raise ConnectorError("Selected field schema changed; rediscover the catalog.")
                # Preserve source order and push down only the selected columns.
                fields = [field for field in schema if field.name in selected]
                plans.append((table, fields))
            for table, fields in plans:
                target = ".".join(quote(part) for part in (table.catalog, table.namespace, table.name) if part)
                sql = f"SELECT {', '.join(quote(f.name) for f in fields)} FROM {target}"
                with self.query(connection, sql) as reader:
                    if [(f.name, f.type) for f in reader.schema] != [(f.name, f.type) for f in fields]:
                        raise ConnectorError("Query schema changed; rediscover the catalog.")
                    for batch in reader:
                        for record in batch_records(batch):
                            yield table, record


def quote(identifier):
    return '"' + identifier.replace('"', '""') + '"'
