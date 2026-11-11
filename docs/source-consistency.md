# Source consistency and shared snapshots

The source integrates the native reusable read view released by
[driver #365](https://github.com/redis/redis-adbc-driver/issues/365) in
[v0.0.24](https://github.com/redis/redis-adbc-driver/releases/tag/v0.0.24), pinned
to `b052eedad9f2fb2c6eeb7c79fe63f8bbc3ca6bdd`. This supersedes the November 2
feasibility record: the driver now provides both atomic statement snapshots and
an explicit connection-scoped shared view. The original connector request was
[#12](https://github.com/Redislabs-Solution-Architects/redis-airbyte-connector/issues/12).

## Configuration and lifecycle

| Configuration | Direct Redis ADBC | Flight SQL |
| --- | --- | --- |
| Omitted or `read_consistency: best_effort` | Ordinary atomic statement snapshots; different tables may use different generations | Existing gateway behavior; no shared-view guarantee |
| `read_consistency: shared_snapshot` | One managed catalog generation for the complete attempt | Rejected before backend creation |

For shared mode, every check, discover or read request opens a dedicated native
connection, sets `adbc.redis.read_snapshot_retention_ms`, and successfully acquires
`adbc.redis.read_snapshot_action=acquire` before any query or metadata call.
Successful acquisition is the capability probe for custom library overrides too.
Unsupported libraries fail with a sanitized error; the connector never downgrades.

Read rediscovers metadata and validates **all** selected schemas inside its own
binding before emitting its first record. An earlier discover request has a
different view. A changed catalog can therefore require rediscovery before the
read begins. GetObjects, GetTableSchema and every subsequent SELECT use the same
connection. Queries and Arrow batches are consumed sequentially; there are no
parallel connections, exported handles or partition workers in this mode.

After readers and cursors close, the connector releases the binding in a finally
path and closes the connection. Failure and generator close/cancellation also
attempt release; cleanup failure does not replace the original read error.
Release errors on otherwise successful completion fail the attempt. Connection
close and server lease expiry provide cleanup fallbacks.

`snapshot_retention_ms` is a fixed lifetime from acquisition, including metadata
planning and streamed consumption: 120000–3600000 milliseconds, default 900000.
It cannot be extended. The driver renews its two-minute lease every 30 seconds
only within that deadline. Process death stops renewal; normal later activity
can collect abandoned pins after lease expiry. An idle server need not delete
retired storage immediately. Individual SELECT deadlines still apply.

Connection snapshot options cannot be supplied in `database_options`; use the
source settings instead. Configuration validation rejects attempts to override
the lifecycle through driver database options.

## Guarantee and limits

The boundary is one published managed catalog generation and its protocol-17
storage in one Redis hash slot. Managed values, schemas and SQL view definitions
are pinned together across supported DML/DDL, TRUNCATE/DROP and compaction.
This is not a coordinated snapshot of independently distributed shards; that
remains [driver #340](https://github.com/redis/redis-adbc-driver/issues/340).

Imported managed copies retain their captured values and provenance. Their
earlier application HASH/index capture remains weaker; later external source TTL
expiry does not expire a managed copy. Mutating/expiring/evicting driver-owned
rows, metadata or indexes outside the protocol is unsupported. Use noeviction,
Search, RESP3 and the documented command/key permissions. Read-only SQL clients
still need permission for snapshot control writes. Current SQL grants and Redis
ACLs apply; snapshots do not preserve revoked row-access rights or retract
already fetched data. Volatile expressions and live server information are not
frozen catalog data. Temporary objects are not supported by the binding.

The driver's existing limits include 128 catalog pins, 16 catalog generations
and bounded control, row and stream resources. Retaining old roots can consume
Redis capacity or backpressure writers. Expiry, lost authority, unavailable
storage and capacity refusal fail explicitly. No partial successful extraction,
replacement view, hidden fresh connection or automatic SQL/input replay is used.

Already emitted records remain provisional until the entire attempt succeeds.
After failure, discard the failed attempt and restart full refresh with a new
connection/view and fresh metadata validation. No durable source checkpoint or
cross-attempt replay is implemented. Append retries can duplicate records;
destination Overwrite does not establish exactly-once delivery.

## Driver upgrade and transport requirements

Both connector images and platform wheels bundle the pinned driver. Source
installs/overrides must supply v0.0.24 or a compatible newer library that passes
acquisition. Installing the separate `adbc-driver-redis` package does not change
the library resolved by this connector's `driver_path`.

The upgraded driver rejects legacy catalogs and has no in-place migration path.
Use a fresh isolated driver database or an explicitly supported managed import
alongside application-owned data without legacy driver metadata. Preserve a
verified backup and previous deployment before changing versions; replacing
binaries does not undo stored data/metadata changes. This applies to both source
and destination. Native macOS artifacts now require macOS 15+ on ARM64; Linux
connector builds retain their Bookworm/glibc 2.36 baseline and use Go 1.27.3.

Flight SQL shared snapshots remain unsupported. The gateway needs its own
metadata/session binding, option propagation and lifecycle implementation and
validation. A native driver upgrade alone cannot provide those guarantees.
Gateway support is tracked in
[redis/redis-apache-arrow-flight#65](https://github.com/redis/redis-apache-arrow-flight/issues/65).

See the pinned [upstream API and lifecycle contract](https://github.com/redis/redis-adbc-driver/blob/v0.0.24/docs/shared-read-snapshots.md)
for authority, resource, unsupported-operation and storage details.
