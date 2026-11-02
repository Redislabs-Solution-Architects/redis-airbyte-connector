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

`read_consistency` defaults to `best_effort`: a full refresh reads live data
without a shared point-in-time view. Requesting `shared_snapshot` currently
fails check, discover and read before a connection is opened, for both native
Redis ADBC and Flight SQL. See [source consistency](../../docs/source-consistency.md)
for the upstream blocker and proposed snapshot contract.

`acceptance-test-config.yml` runs the official spec, successful/failed checks,
discovery, expected-record and repeat full-refresh suites. The release fixture
supplies generated TLS credentials and a dedicated five-row table; no credentials
are committed. Incremental tests are explicitly inapplicable.

The definition ID matches the existing upstream source proposal
[airbytehq/airbyte#90323](https://github.com/airbytehq/airbyte/pull/90323).
Metadata here prepares contribution/review; catalog and Cloud inclusion remain
disabled pending Airbyte maintainer review.
