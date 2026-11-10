# Redis ADBC Airbyte source and destination

A community Airbyte source implementing the initial scope of
[redis/redis-adbc-driver#172](https://github.com/redis/redis-adbc-driver/issues/172).
It consumes an ADBC `RecordBatchReader` and emits Airbyte JSON records through
the Python CDK. Full refresh is the only supported source sync mode. A companion
destination ingests Airbyte records into typed Redis SQL tables using ADBC Arrow
batches, with Append and staged Overwrite.

The upstream source proposal is [Airbyte PR #90323](https://github.com/airbytehq/airbyte/pull/90323).

## Install

Published container images include the native Redis driver and are the primary
installation method for self-managed Airbyte:

```sh
docker pull ghcr.io/redislabs-solution-architects/source-redis-adbc:0.1.3
docker pull ghcr.io/redislabs-solution-architects/destination-redis-adbc:0.1.3
```

Use those full image names when registering custom connectors in Airbyte.

For direct CLI use, install both connectors and the native driver with Python
3.11–3.14:

```sh
python -m pip install redis-airbyte-connector==0.1.3
source-redis-adbc spec
destination-redis-adbc spec
```

The package provides both commands. Platform wheels bundle the native Redis ADBC library for Linux x86_64/ARM64
(glibc 2.36+) and macOS ARM64 (macOS 15+). Windows and macOS Intel are not supported by those
wheels. Source distributions require a separately built native library. Direct Redis connections automatically find the bundled library;
`REDIS_ADBC_LIBRARY` overrides it for custom builds. Redis Search is still
required, and installing the package does not start a Redis server.

## Build and run

```sh
docker build --target runtime -t source-redis-adbc:0.1.3 .
docker run --rm source-redis-adbc:0.1.3 spec
mkdir -p secrets
cp examples/config.json secrets/config.json
# Edit secrets/config.json for your Redis Software or Redis Cloud endpoint.
docker run --rm -v "$PWD/secrets:/config:ro" source-redis-adbc:0.1.3 check --config /config/config.json
docker run --rm -v "$PWD/secrets:/config:ro" source-redis-adbc:0.1.3 discover --config /config/config.json
```

Inside a container, `localhost` refers to that container. On Docker Desktop,
use `host.docker.internal` to reach Redis running on your host, or use a Redis
container's service name on a shared Docker network.

The native Redis driver is built from commit
`b052eedad9f2fb2c6eeb7c79fe63f8bbc3ca6bdd` (v0.0.24) and bundled in the images and platform wheels. Runtime: Python 3.12.12,
Airbyte CDK 7.45.0, ADBC manager/Flight SQL 1.13.0, PyArrow 26.0.0.
Resolved transitive Python dependencies are pinned in `requirements.lock`;
base images are pinned by digest. This uses the CDK's JSON/stdio interface; Arrow batches do not pass through
Airbyte to the destination, and no native Arrow transport or speedup is claimed.

Register `source-redis-adbc:0.1.3` as a custom source in a self-managed Airbyte
installation that can pull your image. Discover the catalog, select streams and
fields, and choose Full Refresh with Overwrite or Append at the destination.
Push the image to your own registry when Airbyte runs on another machine.

For direct CLI reads, create a configured catalog from discovery (see
`examples/catalog.json`) and run:

```sh
docker run --rm -v "$PWD/secrets:/config:ro" source-redis-adbc:0.1.3 \
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

There is no incremental cursor, CDC or durable checkpoint. Failed syncs restart
from the beginning; Append can duplicate already delivered rows, while Overwrite
behavior is controlled by the destination. There is no automatic retry inside a
partially emitted stream. Reader, cursor and connection handles close on
completion, failure or cancellation; SIGTERM interrupts active reads and attempts
ADBC cancellation. SIGKILL cannot run cleanup. Backpressure stops the source
from requesting the next Arrow batch; the driver/server may buffer data.

`read_consistency` defaults to `best_effort`, including when omitted. With the
bundled v0.0.24 driver, each native SELECT has an atomic statement snapshot, but
separate table reads can observe different committed generations.

Set `"read_consistency": "shared_snapshot"` for one managed catalog generation
across metadata validation and every selected table/view in a full-refresh
attempt. Direct Redis ADBC is required. The connector acquires before read-time
rediscovery, reads sequentially on that connection, and releases after readers
close. Each check, discover and read request has its own view; a previous
discover job does not establish the read attempt's view. Flight SQL rejects this
mode before connecting. An unsupported driver, failed acquisition, expiry or
unavailable view fails the attempt without falling back to newer data.

`snapshot_retention_ms` sets the fixed shared-view lifetime: default 900000
(15 minutes), minimum 120000 and maximum 3600000 (one hour). It includes metadata
and complete consumption of all tables; it cannot be extended. Snapshot options
are managed by the connector and cannot be passed through `database_options`.
See [source consistency](docs/source-consistency.md) for limits and retry behavior.

**Driver storage compatibility:** v0.0.24 uses storage protocol 17 and rejects
earlier driver catalogs. There is no in-place upgrade/migration path. Use a fresh
isolated driver database or the driver's explicitly supported managed import
alongside application-owned data without legacy driver metadata. Preserve a
verified backup and the previous deployment before changing driver versions.
This affects both source and destination; replacing the binary does not roll
back stored data. A separately installed `adbc-driver-redis` Python package does
not replace this connector's bundled library; custom `REDIS_ADBC_LIBRARY`
overrides must support successful shared-view acquisition.

## Flight SQL

Set `connection_type` to `flight_sql`, supply a `grpc+tls://` gateway URI and
ADBC Flight SQL database options (for example `username` and `password`). The
same metadata, conversion and full-refresh implementation is used. The gateway
must support ADBC GetObjects, GetTableSchema, autocommit, SQL SELECT and batch
streaming. Redis Search is the gateway operator's responsibility. A gateway is
not bundled. Integration tests require a Redis-backed gateway with TLS and
username/password authentication; the test writes a unique table through native
ADBC and reads it through the gateway to verify the backing database. Gateways without
database schemas can use `"schemas": [""]`.

```sh
bash scripts/test-flight-sql.sh
```

## Write to Redis

Build the companion destination from the same pinned driver/runtime:

```sh
docker build --target destination -t destination-redis-adbc:0.1.3 .
docker run --rm destination-redis-adbc:0.1.3 spec
cp examples/destination-config.json secrets/destination-config.json
# Edit the URI before checking or writing.
docker run --rm -v "$PWD/secrets:/config:ro" destination-redis-adbc:0.1.3 \
  check --config /config/destination-config.json
docker run --rm -i -v "$PWD/secrets:/config:ro" destination-redis-adbc:0.1.3 \
  write --config /config/destination-config.json --catalog /config/catalog.json < records.jsonl
```

Register the image as a custom destination in self-managed Airbyte. Supply a
configured catalog containing the exact incoming stream schema and choose
Append or Overwrite. The destination requires Redis Search, autocommit and SQL
schema/table/ingest permissions. Check creates, ingests, reads and drops a
uniquely named probe table; it can create the configured destination schema.

By default all tables are written to SQL schema `airbyte`, using the stream's
name as the table name. The input namespace identifies records but does not
change the target schema unless `use_stream_namespace` is enabled. Target name
collisions fail before writes. Tables/schemas are created as needed through
the Redis driver's SQL metadata; no arbitrary HASHes are adopted.

| Incoming JSON Schema | Redis ingestion |
| --- | --- |
| Boolean | BOOLEAN |
| Integer | Signed BIGINT; out-of-range values fail |
| Number | DOUBLE; non-finite values and lossy large-integer conversion fail |
| String | VARCHAR; exact integer/decimal strings remain strings |
| Base64 string (`contentEncoding`) | Binary |
| Date/time/date-time string | Arrow date/nanosecond time/timestamp; offsets normalize to UTC for timezone-aware fields |
| Object with explicit properties | Typed nested record |
| Array with an explicit item schema | Typed array |
| Null-only field | Nullable string column |

The destination follows the declared JSON schema. The source intentionally
represents 64-bit integers and decimals as exact strings, so feeding its output
into this destination preserves those strings in VARCHAR columns. It does not
guess that numeric-looking strings should become SQL numbers. Date/time casts
fail outside Arrow's representable range; they do not silently truncate.
Required/null constraints are validated. Optional destination fields accept both
missing and explicit NULL values, including platform-normalized catalogs that
omit the null type union. Array item nullability remains explicit. Unknown fields, ambiguous unions,
dynamic objects, tuple arrays and the driver's reserved `__key` column fail
explicitly. Append requires the existing field names/types to match; schema
evolution requires an explicit migration. Deduplication and CDC are unsupported.

Row and input JSON-byte limits bound aggregate buffers across streams. A single record
may exceed the batch-byte limit up to `max_record_bytes`; input lines have a
hard 16 MiB limit. Malformed lines fail rather than being skipped. No source
record or credential value is included in conversion/driver errors.

Append writes directly and acknowledges each STATE only after all preceding
records are flushed successfully. A failed unacknowledged batch may have
partially persisted because Redis is autocommit-only; retries can duplicate it.
There is no exactly-once claim.

Overwrite builds uniquely named staging tables and preserves the prior target
while input arrives. It defers all STATE acknowledgements until EOF and successful
table promotion. Pending states are spooled to a temporary file, bounded by
`max_state_bytes`; their cursor blobs retain explicit NULLs. Empty streams
replace old data with an empty table. An input failure before promotion drops
staging tables and leaves the prior targets intact. Promotion uses backup/rename
operations and attempts restoration on failure; incomplete recovery retains
`airbyte_backup_*` and `airbyte_stage_*` tables for inspection. If a rename loses
its reply, the writer checks the actual target/staging/backup table layout before
recovering, including after a recovery rename loses its reply. It does not blindly
retry the rename. If metadata cannot establish the outcome, affected tables are
retained and pending STATE acknowledgements are withheld. Stop writers, inspect
the configured targets and retained tables, preserve both previous and staged
data, and restore the intended target names before retrying. Cleanup failures
can leave backups.
This is not an atomic multi-table transaction: readers may briefly see renamed
tables, and process death during promotion can require manual backup recovery.
An ERROR trace or an INCOMPLETE stream-status trace fails the write before
promotion and prevents subsequent STATE acknowledgements. Other stream-status
traces remain informational. Clean EOF is treated as completion. The job runner
must report or abort upstream failures; a source that closes a truncated input
without either failure trace cannot be
distinguished from a completed input by this destination.
Allow only one writer per target, with no concurrent table creation, rename or
deletion in its target/staging/backup names. Recovery identifies tables by their
unique staging/backup names and observed layout; it cannot detect another writer
replacing a table under the same name. Process death bypasses this recovery logic.
Overwrite replaces the table definition and
indexes; it is intended for dedicated destination tables, not application tables.

The destination is direct Redis ADBC only. A production Flight SQL write path
and full Airbyte platform job orchestration remain unvalidated.

Run both destination CLI round trips against the local Enterprise environment
created below:

```sh
python3 scripts/enterprise_env.py e2e
```

## Development and validation

```sh
python3.12 -m venv .venv
.venv/bin/pip install -e '.[test]'
.venv/bin/pytest -m 'not integration'
docker build --target test -t source-redis-adbc-test:0.1.3 .
docker run --rm source-redis-adbc-test:0.1.3 -m 'not integration'
```

Platform-wheel installations use the bundled native driver by default. For
editable/source installations, run `python scripts/build_native.py` first. Set `REDIS_ADBC_LIBRARY`
only when testing a custom native shared library.
All integration tests require a dedicated, disposable Redis Enterprise cluster
(Redis Software or Redis Cloud) with Search, TLS enabled, and a database username
and password. Create the local environment with Python 3.11+, Docker, and OpenSSL:

```sh
python3 scripts/enterprise_env.py up
python3 scripts/enterprise_env.py test
python3 scripts/enterprise_env.py e2e
```

This creates a separate single-node Enterprise development cluster with two
shards, Search, QPF 4x, noeviction, and a dedicated database user. The default
user is disabled. Management and database ports bind only to localhost on
random ports. Generated passwords, a 30-day test CA, certificates, configurations,
and verification evidence are kept under ignored `secrets/enterprise-test/`.
The native and destination test commands build their pinned connector images.
Docker must have sufficient memory for Enterprise and the destination JVM.

To include Flight SQL, supply an existing Enterprise-capable
[Redis Flight SQL server](https://github.com/redis/redis-apache-arrow-flight)
image supporting TLS and Redis ACL authentication:

```sh
python3 scripts/enterprise_env.py gateway --flight-image YOUR_FLIGHT_IMAGE
python3 scripts/enterprise_env.py test
```

The gateway uses the same database user, validates the Enterprise CA, serves
TLS with a trusted test certificate, and authenticates clients against Redis.
Its exact local image ID is recorded in `state.json`. The image must include
support for authenticated TLS Redis connections. The `test` action runs all
seven integration tests when a gateway configuration exists, or the six native
source/destination tests otherwise. No test starts a plaintext Redis or SQLite substitute.

```sh
python3 scripts/enterprise_env.py verify  # TLS, credentials, Search and QPF checks
python3 scripts/enterprise_env.py down    # remove owned containers/network/secrets
```

The environment remains running until `down`. Certificates expire after 30 days;
recreate it with `down` then `up`. This is a single-node development cluster;
it does not exercise multi-node failover. `config.json` uses Docker DNS and
`/enterprise-test/ca.crt`; `host.json` uses localhost and the host's CA path for
native clients. The scripts mount and select these paths automatically.

To use an existing Enterprise deployment instead, configure QPF 4x where
available and provide your own configuration below.

Create `secrets/redis.json` (URL-encode credentials):

```json
{"uri": "rediss://integration-user:URL_ENCODED_PASSWORD@enterprise.example.com:12000/0?read_timeout=30s&write_timeout=30s"}
```

Use a trusted server certificate and configure the driver's CA options in
`database_options` when your cluster uses a private CA. Certificate paths must be
accessible to the process running the driver, including inside containers.
The test configuration asserts your choice of Enterprise deployment; it does
not identify the server edition from its URI. Tests reject plaintext URIs and
missing username/password credentials before connecting.

```sh
export REDIS_ENTERPRISE_CONFIG="$PWD/secrets/redis.json"
.venv/bin/python -m pytest tests/test_integration.py
```

For Flight SQL, also create `secrets/flight.json` pointing to a gateway backed by
the **same** Enterprise database:

```json
{"uri": "grpc+tls://gateway.example.com:31337", "connection_type": "flight_sql", "database_options": {"username": "integration-user", "password": "PASSWORD"}}
```

```sh
export FLIGHT_SQL_CONFIG="$PWD/secrets/flight.json"
.venv/bin/python -m pytest -m integration
```

Explicit integration runs fail when required configuration is absent. Unit tests
remain runnable with `-m 'not integration'`. Native and gateway fixtures create
and drop unique SQL tables. Run the complete destination test with:

```sh
python3 scripts/e2e.py
```

This uses the configured Enterprise cluster over authenticated TLS, starts
PostgreSQL 16.10, runs the source CLI through
`airbyte/destination-postgres:3.0.23` (pinned by digest), verifies loaded data,
and repeats Overwrite to check replay behavior. The Enterprise endpoint must
be reachable from Docker. Containers and the private network are removed on
exit. The Enterprise cluster is externally managed; use a disposable database
and test tables are dropped on exit when the cluster remains reachable.

The manual CI integration job provisions this environment automatically. Its
optional `flight_image` input adds a gateway from an image accessible to the CI
runner; without it, CI runs native integration and destination tests.
See `docs/validation.md` for historical results and validation boundaries.

## Release validation

Pull requests and tagged releases run the same required native TLS integration,
Airbyte acceptance, CLI round-trip and actual Airbyte platform checks. Candidate
images are saved, tested and published without rebuilding; the GitHub release
stays in draft until publication and public artifact verification succeed.
See [docs/releasing.md](docs/releasing.md) for gates, dry runs and recovery.

For private Redis CAs in Airbyte, set `tls_ca_certificate` to the PEM trust bundle.
Both connectors retain it in a private temporary file for the connection lifetime,
verify TLS normally and remove it when the connection closes. This avoids requiring
an operator-managed CA file mount in each Airbyte workload.
