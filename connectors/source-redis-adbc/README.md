# Redis ADBC source

Community, full-refresh-only source for Redis Enterprise with Search. Direct
Redis ADBC and a separately operated Flight SQL gateway are supported. The
source discovers SQL metadata and emits Airbyte JSON records; it does not scan
arbitrary Redis HASH collections or add native Arrow transport to Airbyte.

Use `ghcr.io/redislabs-solution-architects/source-redis-adbc:<version>` as a custom
source in self-managed Airbyte. Supply a `rediss://` URI with named-user
credentials. For a private CA, paste its PEM trust bundle into
`tls_ca_certificate`; certificate verification remains enabled. Omit unused
optional database options rather than submitting an empty secret object on
the pinned platform.

The package `redis-airbyte-connector` also provides `source-redis-adbc` for direct
CLI use. See the repository README for schemas, supported Arrow types,
configuration, deployment and consistency boundaries.

`read_consistency` defaults to `best_effort`, with separate read views for each
table. Native `shared_snapshot` binds read-time metadata and all selected
managed table/view reads to one catalog generation using the bundled Redis ADBC
v0.0.24 driver. `snapshot_retention_ms` defaults to 15 minutes, with a fixed
maximum of one hour; expiry fails the attempt without fallback. Flight SQL
rejects this mode before connecting. See [source consistency](../../docs/source-consistency.md)
for the lifecycle, retry limits and protocol-17 fresh-storage requirement.

`acceptance-test-config.yml` runs the official spec, successful/failed checks,
discovery, expected-record and repeat full-refresh suites. The release fixture
supplies generated TLS credentials and a dedicated five-row table; no credentials
are committed. Incremental tests are explicitly inapplicable.

The definition ID matches the existing upstream source proposal
[airbytehq/airbyte#90323](https://github.com/airbytehq/airbyte/pull/90323).
Metadata here prepares contribution/review; catalog and Cloud inclusion remain
disabled pending Airbyte maintainer review.
