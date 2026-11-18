# Validation record

## November 18, 2026 v0.2.1 preparation

Local checks validated connector commit
`7f3a72403061d97e24d1819fb94f02a41dac9a74`, based on main
`97920f0a66c88be5ba0bc63eb2c1e2c40627b0f3`. The bundled driver was rebuilt
from released Redis ADBC [v0.0.29](https://github.com/redis/redis-adbc-driver/releases/tag/v0.0.29),
commit `30a9eefbf13d19b3e3129171f8ecdc5579b852a2`, using Go 1.27.4.
Earlier records retain their original driver versions and validation scope.

- Python 3.14.10 on macOS ARM64: 194 non-integration tests passed.
- Undefined-name lint and source/destination catalog metadata checks passed.
- Source distribution and macOS ARM64 wheel built; Twine checks passed.
- The wheel installed into a separate environment. Outside the checkout,
  both CLI `spec` commands succeeded and the bundled native library loaded
  with the `AdbcDriverRedisInit` entry point present.

These local checks do not establish Linux, native TLS integration, Airbyte
acceptance or platform-sync qualification. Hosted checks are tracked in
[release preparation PR #25](https://github.com/Redislabs-Solution-Architects/redis-airbyte-connector/pull/25);
the tagged publishing workflow must pass all release gates and verify public
artifacts before finalizing the GitHub release.

## November 17, 2026 dependency refresh

Validated the working tree based on connector commit
`0b0a5c03316e6ba2a050227bf489786f44ea9678`. The SHA-256 of
`git diff --binary -- . ':!docs/validation.md'` was
`96513d18776f92350b5ab128145f798ec1f98c07a2bd2cc880068b30b21d9b09`.
Earlier records below continue to describe their original revisions.

The bundled driver was rebuilt from released Redis ADBC
[v0.0.28](https://github.com/redis/redis-adbc-driver/releases/tag/v0.0.28),
commit `b63a010d88d2b69707594cb9dc063992dac06b32`, using Go 1.27.3.
Python is now 3.12.15, Airbyte CDK 7.49.0 and PyArrow 26.0.1.
ADBC manager/Flight SQL 1.13.0, pytest 9.2.0 and MkDocs 1.6.1 were
already current. Ruff and the versioned GitHub Actions were refreshed.
The Linux/Python 3.12 lock was regenerated with the command in its header.

The dependency resolver retained twelve older transitive releases because the
latest CDK restricts them: protocol models, boltons, genson, isodate, nltk,
pandas, pyrate-limiter, python-dateutil, python-ulid, wcmatch, whenever and
xmltodict. No CDK constraints were overridden. `jsonschema` is now an explicit
dependency rather than an incidental transitive import.

Standard-library `hashlib.file_digest` replaces the custom chunked hashing loop
and whole-file release hashing. Artifact tamper-detection tests still pass.
The new CDK serializer still removes NULL-valued arbitrary dictionary entries
and checkpoint IDs, so the source/destination protocol workarounds remain.
PyArrow still cannot cast strings directly to time64, so the strict nanosecond
time parser remains. The native release still does not support unmanaged
Arrow replace, so staged overwrite and lost-reply recovery remain necessary.

Validation:

- 187 non-integration tests passed on macOS ARM64 and Linux ARM64.
- Nine native integration tests passed on macOS ARM64 against a newly owned,
  two-shard Redis Enterprise fixture with named-user authentication, trusted TLS,
  Search and QPF 4x. This includes the real 121-second snapshot expiry test and
  the exact native driver version assertion.
- Nine native integration tests also passed on Linux ARM64 candidate image
  `sha256:09bd54507e96784e61f5dd8815c0cd05dc0f6d40297996f80eeb1961e4997261`,
  including real snapshot expiry. After rebuilding to include the explicit
  `jsonschema` dependency declaration, the final Linux ARM64 runtime image,
  `sha256:91671392717361dffc21267fccbf54b75cdddc04bf520337d9df2d8c18876896`,
  passed the eight native tests excluding expiry.
- The macOS native library and platform wheel rebuilt; the wheel and sdist
  passed `twine check`. A fresh environment installed the wheel, passed
  dependency checks, ran both CLI specifications and loaded the native entrypoint.
- Undefined-name lint, metadata validation, strict MkDocs build, actionlint and
  Python dependency compatibility checks passed.

The local macOS linker warned that cached objects targeted macOS 26 while
linking for macOS 15. These results validate the current host, not the macOS 15
minimum. AMD64, hosted GitHub Actions execution, official CAT, full Airbyte
platform syncs and Flight SQL were not rerun in this refresh. Historical results
below are not evidence for these new dependencies.

## Overwrite lost-reply recovery

On November 2, 2026, deterministic stateful cursor tests exercised rename errors
both before and after applying each forward rename across two streams, and lost
replies after each rollback rename. Assertions compare retained table contents,
verify restoration of previously absent targets, and ensure pending STATE
messages are withheld. Metadata failures, unexpected table layouts and failed or
unverifiable recovery retain previous and staged data without destructive cleanup.
The non-integration suite passed all 161 tests; seven integration tests were
excluded from this run.

These simulations do not qualify live Redis lost-reply behavior, reconnect
behavior, process death or concurrent writers. Recovery remains autocommit and
requires exclusive ownership of the target and generated staging/backup names.

## Release gate validation

Validated on November 1, 2026, in the release-validation branch, without changing
the published 0.1.1 artifacts:

- 126 unit/CLI tests passed; metadata checks, actionlint, source-distribution
  build and `twine check` passed.
- Undefined/unbound name checks passed. Docker network tests confirmed that the
  default network attachment can change a node's gateway; lower fixture-network
  priority preserves the Kubernetes route. Cleanup was verified on macOS and
  Linux, including private persisted files and the generated kubeconfig.
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
- The same platform flow passed on a fresh Ubuntu AMD64 hosted runner using the
  saved, tested image artifacts. Custom definitions use Airbyte's authenticated
  workspace-scoped UI endpoints; registration, checks, discovery, all three
  successful sync jobs, cancellation, exact row comparisons and cleanup passed.

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
earlier connector was full-refresh only, without snapshot isolation, CDC, a
persistent checkpoint, native Arrow transport or a performance claim. Its
concurrent-write/TTL limitations predate the native shared-view integration below.
The container was executed on ARM64; an AMD64 release build has not been
validated here.

Upstream maintainer guidance on an in-tree Airbyte integration and any new
batch transport remains separate work in the existing upstream discussion.

## Native shared-view integration — November 10, 2026

The connector now pins released Redis ADBC v0.0.24,
`b052eedad9f2fb2c6eeb7c79fe63f8bbc3ca6bdd`, and integrates its connection-scoped
read view. This supersedes the historical absence of snapshot support above.
Flight SQL shared snapshots remain explicitly unsupported.

Local Python 3.12.16 checks: 185 non-integration tests passed; 10 integration tests
were deselected. Ruff F821/F823, metadata consistency and whitespace checks passed.

Native macOS ARM64 tests loaded the published v0.0.24 archive after verifying
SHA-256 `5f9ed08bd950dc825a6920e41937d06f93e35a21af4382f7cf2a66a417c30d2b`
against its release sidecar. Two shared-view tests passed, followed by all six
existing source/destination integration tests and the real snapshot-expiry test
(seven passes in that second selection). The fixture was this checkout's
separate two-shard Redis Software 8.2.0-78.18 database, with Search, verified
active 4x QPF, noeviction, named-user authentication and CA-verified TLS.

The consistency regression acquires before read-time discovery, then uses a
second client to update one table, add a column and insert into another, and
drop a selected view. Complete old schemas, exact 64-bit values, NULLs and row
multiplicities remain correct for all three streams; a fresh discover sees the
new schema and missing view. Separate tests verify generator-close release and
failure after partial emission when the real 120000 ms lifetime expires.

The Docker test image is built from the pinned driver source with the
checksummed Go 1.27.3 Bookworm base and the existing Python 3.12.12 runtime.
All nine source/destination/shared-view integration tests passed in that Linux
ARM64 container (123.86 seconds), including the real expiry test.
The new integration module is included in the normal native CI test selection.
Hosted AMD64/platform validation, Flight SQL shared views and a new connector
release are separate evidence; no publication or performance claim is made here.


## Python 3.14 minimum and Redis ADBC v0.0.28 — November 17, 2026

Validated connector revision `185d35a50efbf3a63e1efe855b2f995adc1386f0` with
Python 3.14.10 and driver v0.0.28 at
`b63a010d88d2b69707594cb9dc063992dac06b32`. The runtime uses the Dockerfile's
pinned Python 3.14.10 Bookworm image; all 85 runtime dependency pins matched
its installed `pip freeze` output, and dependency checks passed.

All 187 non-integration tests passed on macOS ARM64 and inside the Linux ARM64
container. Native libraries were rebuilt from the pinned source for both
platforms and loaded successfully. All nine source, destination and shared-view
integration tests passed in the Linux ARM64 runtime (123.86 seconds), including
the real snapshot-expiry test and the v0.0.28 GetInfo assertion. The fixture was
this checkout's disposable two-shard Redis Software 8.2.0-78.18 instance with
Search, QPF 4x, named-user authentication and CA-verified TLS; it was removed
after validation.

The sdist and macOS ARM64 wheel built and passed Twine checks. Wheel metadata
accepts Python 3.14 and rejects 3.13, retaining the existing 3.15 upper bound.
Both installed-wheel CLI spec commands and native loading passed outside the
checkout. Ruff F821/F823, connector metadata, whitespace checks and the strict
MkDocs build passed. The macOS linker warned about host objects targeting a
newer OS than macOS 15; loading was checked only on the current host. Integration
warnings included third-party deprecations, unregistered marks and a read-only
pytest cache; all tests passed. Hosted AMD64, macOS 15 runtime compatibility,
Airbyte acceptance/platform and Flight SQL checks were not rerun for this revision.


### Revalidation after merging current main

Revision `f78ddb0b0a3282db0e99bc639dca662b9d8dd31a` merges main at
`ec3979962569c3254805c61af60a919860e73ea7`, including the dependency refresh
and qualified Flight SQL snapshot support, while retaining the Python 3.14
minimum and Python 3.14.10 runtime pin. The Linux dependency resolution for
Python 3.14 retained all 85 refreshed pins from main.

All 194 non-integration tests passed on macOS ARM64 and in the rebuilt Linux
ARM64 Python 3.14.10 container; 12 integration tests were deselected. Dependency
checks passed in both environments. Ruff F821/F823, connector metadata,
whitespace checks and the strict MkDocs build passed. This revalidation did not
rerun the native, Flight SQL, acceptance or platform integration suites; earlier
results above and in the Flight snapshot validation record apply to their
specified revisions.
