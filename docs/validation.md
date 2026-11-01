# Validation record

Validated on November 1, 2026, on Docker Desktop, Linux ARM64 containers.

## Components

| Component | Pinned version |
| --- | --- |
| Source Python runtime | 3.12.12, Debian Bookworm (base image digest in Dockerfile) |
| Airbyte Python CDK | 7.45.0 |
| Airbyte protocol Python models | 0.17.1 |
| ADBC driver manager / Flight SQL | 1.13.0 |
| PyArrow | 26.0.0 |
| Redis native driver | `redis/redis-adbc-driver@b99ec0aa3b61ed5db130211951d24191217efae8` |
| Redis Software | `redislabs/redis:8.2.0-78.18`, Redis 8.6.2, Search, one RAM shard, noeviction, QPF 4x |
| Airbyte PostgreSQL destination | 3.0.23, digest `sha256:1a122ed76f97b45fcdf7a8183bed93330a8f8eedad365e883d738774a35d69ea` |
| PostgreSQL | 16.10 |
| Flight SQL reference gateway | Apache Arrow Go 18.8.0 SQLite example server |

## Coverage

Final test results: 50 unit/CLI tests and 3 native Redis tests passed in the
packaged Python 3.12 image. The separate Flight SQL reference-server run passed
its integration test (54 tests total). Both PostgreSQL destination syncs passed.

- Unit and CLI tests: explicit conversions, large integers, decimal precision,
  dictionary values, binary, nanosecond timestamps, NULLs, nested values,
  unsupported types, non-finite numbers, exact schema filters, ambiguous
  catalogs, quoted identifiers, projection, drift failures, full-refresh-only
  enforcement, lazy batch requests, connection failure, late read failure and
  reader/cursor/connection cleanup on cancellation.
- Native Redis tests: check, discovery, projection, five records across at least
  three Arrow batches, empty results, repeat reads and abandoned readers.
- PostgreSQL destination test (`python3 scripts/e2e.py`): the source's actual
  spec/check/discover/read CLI and the official destination's check/write CLI.
  Five rows and an empty stream reach final PostgreSQL tables. Values include
  `9007199254740993`, `12345678901234567890.123456789`, explicit NULLs, booleans,
  Base64 binary and microsecond timestamps. The test compares final selected
  column values, checks that the empty table has zero rows, and repeats with a
  new Overwrite generation to verify exactly five rows remain.
- Failure/retry: an invalid selected stream fails before emitting records;
  a fresh full-refresh read succeeds. A failed native connection does not expose
  its password in CLI stdout/stderr. Late read errors and cancellation are
  separately injected in deterministic backend tests.
- Flight SQL (`bash scripts/test-flight-sql.sh`): an actual ADBC Flight SQL
  connection to Apache's SQLite reference server exercises check, metadata,
  schema discovery and selected-field reads, including a NULL row, through the
  same backend and source classes used for Redis.

The destination test supplies `generation_id`, `minimum_generation_id` and
`sync_id`, which the Airbyte platform normally adds. It invokes the connector
processes directly over JSON/stdio; it does not test a full Airbyte platform
deployment or the custom-source registration UI.

The first destination run exposed that CDK 7.45.0's standard serializer removes
NULL entries inside arbitrary record dictionaries. The connector uses the
CDK parser/command handlers and its own record-preserving serialization shim.
A regression test verifies nested and top-level NULLs on the serialized wire.

## Redis write destination

The companion destination adds spec/check/write and ADBC bulk ingestion with
Append and staged Overwrite. It uses the same pinned Python/native-driver
runtime as the source. Check tests an actual probe-table write, read and cleanup.

Expanded suite results: 92 unit/CLI tests and 6 native Redis source/destination
tests passed together (98 passed, 1 Flight SQL test skipped in that run). The
separate actual Flight SQL test also passed: 99 tests total. Both connector
images built, and the Redis destination/source/destination CLI round trip passed.

The write tests cover typed/nested arrays and records, nanosecond timestamps
and clock times, explicit NULLs, exact string values, binary decoding,
schema preflight, bounded row/JSON-byte batches, malformed input, incomplete
bulk counts and state acknowledgement ordering. Legacy, per-stream and global
state cursor blobs retain NULL values. Overwrite states are deferred until
EOF and promotion; failure tests exercise rollback across multiple tables and
retention of backups if recovery fails. A source ERROR trace also prevents
Overwrite publication.

Three native destination tests on Redis Software/Search verify a five-row
typed write/read round trip, Append, failed Overwrite preserving old data,
staging cleanup, retry, cancelled input and empty Overwrite. The separate
`python3 scripts/e2e_redis.py` exercises both actual connector CLIs:

```text
Airbyte records -> Redis destination -> Redis source -> Redis destination
```

It compares all final values, including a large integer, exact decimal string,
binary, nanosecond timestamp, NULL and nested array. It also compares exact
per-stream/global state blobs and verifies a failed CLI Overwrite does not
acknowledge a checkpoint or change the prior target. Retry and empty Overwrite
then succeed.

Overwrite is a sequence of autocommit rename/backup operations, not an atomic
multi-table transaction. Only one writer per target is supported. A crash during
promotion can need manual backup recovery. Append can persist part of a failed
unacknowledged batch and duplicate it on retry; exactly-once behavior is not
claimed. Numeric-looking strings remain VARCHAR values according to the schema.
Clean EOF finalizes Overwrite; the job runner must report or abort upstream
failures rather than close an incomplete input as if it completed successfully.
Flight SQL destination writes and full Airbyte platform orchestration are not
validated.

## Remaining boundaries

Redis-backed production Flight SQL gateways, Flight TLS/authentication, deployed
Redis Cloud TLS/ACL combinations, destination outage mid-write and platform job
cancellation/retry orchestration require deployment-specific validation. The
connector is full-refresh only, with no snapshot isolation, CDC, persistent
checkpoint, native Arrow transport or performance claim. Concurrent writes and
TTL expiry are documented consistency limitations. The container was executed
on ARM64; an AMD64 release build has not been validated here.

Upstream maintainer guidance on an in-tree Airbyte integration and any new
batch transport remains separate work in the existing upstream discussion.
