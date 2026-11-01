# Redis ADBC Airbyte source

A community Airbyte source implementing the initial scope of
[redis/redis-adbc-driver#172](https://github.com/redis/redis-adbc-driver/issues/172).
It consumes an ADBC `RecordBatchReader` and emits Airbyte JSON records through
the Python CDK. Full refresh is the only supported sync mode.

## Build and run

```sh
docker build --target runtime -t source-redis-adbc:0.1.0 .
docker run --rm source-redis-adbc:0.1.0 spec
mkdir -p secrets
cp examples/config.json secrets/config.json
# Edit secrets/config.json for your Redis Software or Redis Cloud endpoint.
docker run --rm -v "$PWD/secrets:/config:ro" source-redis-adbc:0.1.0 check --config /config/config.json
docker run --rm -v "$PWD/secrets:/config:ro" source-redis-adbc:0.1.0 discover --config /config/config.json
```

Inside a container, `localhost` refers to that container. On Docker Desktop,
use `host.docker.internal` to reach Redis running on your host, or use a Redis
container's service name on a shared Docker network.

The native Redis driver is compiled into the image from commit
`b99ec0aa3b61ed5db130211951d24191217efae8`. Runtime: Python 3.12.12,
Airbyte CDK 7.45.0, ADBC manager/Flight SQL 1.13.0, PyArrow 26.0.0.
Resolved transitive Python dependencies are pinned in `requirements.lock`;
base images are pinned by digest. This uses the CDK's JSON/stdio interface; Arrow batches do not pass through
Airbyte to the destination, and no native Arrow transport or speedup is claimed.

Register `source-redis-adbc:0.1.0` as a custom source in a self-managed Airbyte
installation that can pull your image. Discover the catalog, select streams and
fields, and choose Full Refresh with Overwrite or Append at the destination.
Push the image to your own registry when Airbyte runs on another machine.

For direct CLI reads, create a configured catalog from discovery (see
`examples/catalog.json`) and run:

```sh
docker run --rm -v "$PWD/secrets:/config:ro" source-redis-adbc:0.1.0 \
  read --config /config/config.json --catalog /config/catalog.json
```

## Data and access requirements

Redis Search is required. Autocommit is always enabled. Tables and views must
already be exposed by the Redis ADBC driver's metadata. The default discovered
schema is `public`; configure exact `schemas` to include other schemas. ADBC
metadata, not a scan of arbitrary Redis keys, defines the source catalog. This
connector does not adopt or alter application HASH collections. Consult the
[driver documentation](https://github.com/redis/redis-adbc-driver) to explicitly
prepare external data. Opening the upstream driver can initialize or migrate
its own metadata, so its connection permissions and side effects still apply.

Use `rediss://` for TLS and URL-encode URI credentials. `uri` and
`database_options` are marked secret. Raw driver errors are withheld because
they may contain passwords or connection strings. The connector validates
configuration, fully consumes the check query, and checks metadata access.
Set URI `read_timeout` and `write_timeout` to bound blocking Redis operations.
Use a trusted driver configuration; database options are passed to native code.

Only selected streams and top-level fields are read. Stream names are table
names, and Airbyte namespaces are database schemas. An optional exact `catalog`
resolves duplicate schema/table names across catalogs. SQL identifiers are quoted.
Schema drift fails the sync and requires rediscovery. Unsupported Arrow types
fail explicitly. Empty results emit no records.

| Arrow type | Airbyte JSON representation |
| --- | --- |
| NULL | `null` |
| Boolean, integers up to 32 bits | Boolean, JSON integer |
| Signed/unsigned 64-bit integer | Decimal string, preserving values beyond 2^53 |
| Decimal | Fixed-point string, preserving precision and scale |
| Float | JSON number; NaN and infinities fail the sync |
| UTF-8 / dictionary | String / decoded value |
| Binary | Base64 string |
| Date, time, timestamp | ISO string; timestamps retain stored precision and timezone |
| List, fixed-size list, struct | Recursively converted array/object |
| Map, union, interval, duration, extension types | Rejected |

There is no incremental cursor, CDC, durable checkpoint, or snapshot isolation.
Concurrent writes and TTL expiry can change results during a read. Failed syncs
restart from the beginning; Append can duplicate already delivered rows, while
Overwrite behavior is controlled by the destination. There is no automatic retry
inside a partially emitted stream. Reader, cursor and connection handles close
on completion, failure or cancellation; SIGTERM interrupts active reads and
attempts ADBC cancellation. SIGKILL cannot run cleanup. Backpressure stops the
source from requesting the next Arrow batch; the driver/server may buffer data.

## Flight SQL

Set `connection_type` to `flight_sql`, supply a `grpc+tls://` gateway URI and
ADBC Flight SQL database options (for example `username` and `password`). The
same metadata, conversion and full-refresh implementation is used. The gateway
must support ADBC GetObjects, GetTableSchema, autocommit, SQL SELECT and batch
streaming. Redis Search is the gateway operator's responsibility. A gateway is
not bundled. The generic path is tested against Apache Arrow Go's pinned
SQLite reference Flight SQL server. A deployed Redis-backed gateway, TLS and
authentication must still be validated for that deployment. Gateways without
database schemas can use `"schemas": [""]`.

```sh
bash scripts/test-flight-sql.sh
```

## Development and validation

```sh
python3.12 -m venv .venv
.venv/bin/pip install -e '.[test]'
.venv/bin/pytest -m 'not integration'
docker build --target test -t source-redis-adbc-test:0.1.0 .
docker run --rm source-redis-adbc-test:0.1.0 -m 'not integration'
```

For local Python use, set `REDIS_ADBC_LIBRARY` to a built native shared library.
Native tests require a dedicated, disposable Search-enabled Redis database:

```sh
REDIS_URI='redis://localhost:12000/0' \
REDIS_ADBC_LIBRARY=/absolute/path/libadbc_driver_redis.so \
.venv/bin/pytest -m integration
```

The integration fixture creates and drops its own uniquely named SQL tables.
Run the complete disposable destination test with:

```sh
python3 scripts/e2e.py
```

This starts Redis Software with Search and QPF 4x and PostgreSQL 16.10, runs
the source CLI through `airbyte/destination-postgres:3.0.23` (pinned by digest),
verifies actual loaded data, then repeats Overwrite to check replay behavior.
It removes its containers and private network on exit. Allow several minutes
for Software bootstrap and destination startup. See
`docs/validation.md` for results and remaining validation boundaries.
