# Validation record

## Release gate validation

Validated on November 1, 2026, in the release-validation branch, without changing
the published 0.1.1 artifacts:

- 126 unit/CLI tests passed; metadata checks, actionlint, source-distribution
  build and `twine check` passed.
- Six native authenticated TLS Enterprise tests passed against the candidate
  images. Both PostgreSQL syncs and the Redis CLI round trip passed, including
  failed Overwrite, retry, empty Overwrite and exact checkpoint preservation.
- The official pinned CAT source suite passed 36 tests (two skipped, two
  historical-compatibility tests deselected); the destination suite passed 25
  tests (four source-only tests skipped, one historical test deselected).
  These runs do not claim historical-image backward compatibility.
- A real local Airbyte 2.0.1 installation registered and checked both custom
  connectors, discovered the source, completed two full-refresh Overwrite jobs,
  cancelled a queued job, and completed a manual restart. Five destination rows
  retained exact large integer and decimal strings and NULLs. Cancellation
  preserved the previously published target. This tests queued cancellation;
  it does not claim to interrupt an active platform write.

The real platform test exposed two destination compatibility issues now covered
by regression tests: Airbyte's catalog normalization removes optional NULL unions,
and the CDK parser omits the platform's checkpoint ID. Optional fields accept
NULL after normalization, while required fields retain their constraints. The
destination preserves complete incoming state envelopes through parsing, deferred
Overwrite spooling and acknowledgement, including unknown platform fields.

The reusable CI runs native AMD64/ARM64 integration and acceptance checks and an
AMD64 platform check. The release-only validation also builds and installs all
three platform wheels; hosted run results are attached to the pull request.

## Version 0.1.1 packaging validation

Validated on November 1, 2026, with the native driver pinned to
`b99ec0aa3b61ed5db130211951d24191217efae8`:

- 104 unit/CLI tests passed on macOS ARM64.
- A macOS ARM64 platform wheel built, passed `twine check`, and installed into a
  fresh Python environment. Both CLI specifications ran, and the installed
  library loaded and exported `AdbcDriverRedisInit`. Its minimum macOS version
  was verified as 12.0.
- All six native source/destination tests passed on a newly provisioned,
  authenticated TLS Redis Enterprise database in Linux ARM64 containers.
- Both PostgreSQL destination syncs and the Redis destination/source/destination
  CLI round trip passed, including nested values, exact state preservation,
  failed Overwrite, retry, and empty Overwrite.
- The current PyPI `adbc-driver-redis==0.0.19` library was tested and rejected as
  a packaging dependency because it lacks nested-struct ingestion. Version
  0.1.1 bundles the validated revision directly in platform wheels and images.

The Flight SQL integration test was not rerun for this packaging change. The
prior authenticated TLS gateway validation remains recorded below. Release CI
also builds and loads the native wheel on Linux AMD64 and ARM64 and smoke-tests
the connector images on both architectures before completing publication.

The current authenticated TLS environment was validated on November 1, 2026:

- Unit suite: 102 tests passed.
- `python3 scripts/enterprise_env.py test`: all 7 integration tests passed
  (3 native source, 3 native destination and 1 Flight SQL), against the same Enterprise database.
- `python3 scripts/enterprise_env.py e2e`: both destination syncs passed;
  all 5 rows and the empty stream were verified in PostgreSQL after each sync.
  The Redis destination/source/destination CLI round trip also passed, including
  exact value/state preservation, failed Overwrite, retry and empty Overwrite.
- Environment checks passed: CA-verified management and database TLS, named-user
  authentication, default user disabled, two database shards, Search, QPF 4x,
  noeviction, rejection of plaintext, missing authentication, incorrect
  passwords, and an untrusted CA.

The fixture uses `redislabs/redis:8.2.0-78.18` on a single development node with
2 GB database memory and no replication. The Flight SQL test used the locally
available Enterprise-capable image
`sha256:14f074a4bf66d5c7caaec9b9bb6111c8cf5f9601aa1e1792c76ab86e9c31a0b9`.
Generated configuration and verification evidence live in the ignored
`secrets/enterprise-test/` directory; they contain credentials and are not
committed. See the README for recreation and teardown commands.

The results below describe the earlier setup and are retained as history.

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

Other production Flight SQL gateways, deployed Redis Cloud TLS/ACL combinations,
multi-node failover, destination outage mid-write and platform job
cancellation/retry orchestration require deployment-specific validation. The
connector is full-refresh only, with no snapshot isolation, CDC, persistent
checkpoint, native Arrow transport or performance claim. Concurrent writes and
TTL expiry are documented consistency limitations. The container was executed
on ARM64; an AMD64 release build has not been validated here.

Upstream maintainer guidance on an in-tree Airbyte integration and any new
batch transport remains separate work in the existing upstream discussion.
