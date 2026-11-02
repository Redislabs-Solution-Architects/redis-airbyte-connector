# Source consistency and shared snapshots

Decision for [connector issue #12](https://github.com/Redislabs-Solution-Architects/redis-airbyte-connector/issues/12), November 2, 2026:
atomic source snapshots are blocked by the current upstream interfaces. Add an
explicit consistency requirement and reject unsupported snapshot requests. Do
not advertise retained query output as an atomic snapshot.

## Current contract

| Configuration | Direct Redis ADBC | Flight SQL |
| --- | --- | --- |
| `read_consistency` omitted or `best_effort` | Existing live full refresh, autocommit enabled | Existing live full refresh, autocommit enabled |
| `read_consistency: shared_snapshot` | Reject before creating the backend | Reject before creating the backend |

Check returns FAILED for an unsupported requirement. Discover and read raise a
safe error; read emits no records. Invalid consistency values fail configuration
validation. No automatic downgrade occurs. The setting applies to the source;
the destination does not offer snapshot isolation.

The source uses one connection and sequential SELECT statements for the selected
tables/views. It validates their schemas before emitting records, but neither
this validation nor connection reuse freezes metadata or rows. Updates, deletes,
inserts, TTL expiry and DDL may affect a read, including across batches of one
query. There is no cross-table, cross-schema or cross-shard consistency boundary.
The connector currently does not schedule partition workers.

A failed best-effort sync restarts from the beginning against current data. It
does not resume a snapshot. Append retries can duplicate delivered records;
destination Overwrite does not make source extraction point-in-time consistent.

## Upstream feasibility evidence

The connector pins driver commit
`b99ec0aa3b61ed5db130211951d24191217efae8` in `scripts/build_native.py`.
The assessment is scoped to that driver and this connector's gateway interface.

- [Connection implementation](https://github.com/redis/redis-adbc-driver/blob/b99ec0aa3b61ed5db130211951d24191217efae8/go/connection.go):
  disabling autocommit is rejected; Commit and Rollback are unsupported.
- [Source-consistent capture assessment](https://github.com/redis/redis-adbc-driver/blob/b99ec0aa3b61ed5db130211951d24191217efae8/docs/partition-coverage.md#source-consistent-capture-assessment):
  atomic capture is unavailable for ordinary SQL, live partitions and retained
  partitions. Before/after counts, schema checks and checksums cannot establish
  a snapshot under concurrent mutations. The assessment also records that
  transaction/consistency development is outside the active driver roadmap.
- [Retained result contract](https://github.com/redis/redis-adbc-driver/blob/b99ec0aa3b61ed5db130211951d24191217efae8/docs/retained-partition-results.md):
  completed query output can be replayed, but capture can contain mixed-time
  values. The completed [driver issue #169](https://github.com/redis/redis-adbc-driver/issues/169)
  addresses that replay contract, not atomic source snapshots across tables.
- The connector's Flight SQL path uses autocommit and exposes no snapshot
  begin/export/import/release API. A gateway's support for SQL, Arrow batches or
  transactions alone is insufficient evidence of the required shared boundary.

The connector therefore cannot implement #12's atomic guarantee by turning off
autocommit, recording a timestamp, buffering batches, retaining each table's
result separately or retrying until row counts match. Pausing writers alone
also does not address TTL expiry. An externally managed immutable copy may be
an operational alternative, but this connector does not create or verify one.

## Contract required before implementation

An upstream driver/server or separately designed snapshot service must first
provide a verifiable consistency boundary covering all selected table data,
schemas, view definitions and shards. Its contract must define how concurrent
inserts, updates, deletes, expiry and DDL are isolated. A token must reference
that boundary, rather than merely identify a job or completed query output.

The connector can enable `shared_snapshot` only after the following are defined
and implemented for each supported transport:

1. Acquire the snapshot before metadata planning or any records are emitted.
   Bind all reads and any future partition workers to the same boundary through
   authenticated handles. Reject any selected object or deployment outside it.
2. Specify ownership, authorization, fixed lifetime, any lease renewal, quotas
   and limits on memory/storage/retention. Enforce bounded creation/read timeouts
   and cancellation; resource exhaustion must fail rather than truncate output.
3. Release owned handles on success, failure and cancellation. Define bounded
   server-side expiry/cleanup for process death and abandoned workers. Expired,
   revoked or lost snapshots fail the sync without switching to live data.
4. Define whether the same snapshot can be reused after a failed sync. Until a
   durable ownership/checkpoint protocol exists, retries start a new sync and
   acquire a new snapshot; no cross-attempt replay guarantee is implied. A source
   snapshot does not itself provide exactly-once destination effects.

## Validation and remaining work

Connector unit tests exercise both transports through check, discover and read:
unsupported requests and invalid values fail before backend creation; omitted
and explicit best-effort configurations preserve full-refresh output. Existing
reader cleanup/cancellation tests continue to apply to best-effort extraction.

There is no implemented snapshot lifecycle to test yet. Enabling it requires
authenticated TLS integration tests that modify related tables during capture
(including inserts, updates, deletes, TTL expiry and DDL) and verify one boundary
across batches, tables and shards. Future workers must prove they share it.
Tests must also cover quota exhaustion, acquisition failure, expiry/revocation,
partial consumption, retries, cancellation, process death and bounded cleanup.
Native and Redis-backed Flight SQL need separate evidence; mocked capability
responses or a SQLite gateway do not validate Redis source consistency.

Issue #12 remains open for that upstream capability and integration work. The
configuration guard and this decision record do not constitute snapshot support.
