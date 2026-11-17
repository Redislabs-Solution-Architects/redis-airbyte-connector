# Redis ADBC destination

Community destination that ingests Airbyte JSON records into typed Redis SQL
tables through Arrow ADBC. Append and staged Overwrite are supported. Redis
Enterprise with Search, named-user authentication and write/schema permissions
is required.

Use `ghcr.io/redislabs-solution-architects/destination-redis-adbc:<version>` as a
custom destination in self-managed Airbyte. Set `schema` to a dedicated target
schema and configure `rediss://` plus `tls_ca_certificate` for private CAs.
Connection checks create and drop a uniquely named probe table. Omit unused
optional database options rather than submitting an empty secret object on
the pinned platform.

Optional fields accept absent or explicit NULL values. Required/non-null fields
and array-item types remain validated. Numeric-looking source strings remain
VARCHAR values. Append retries may duplicate partially persisted rows. Overwrite
keeps prior targets until EOF, but promotion is a sequence of autocommit renames;
it is not an atomic multi-table transaction, and crashes can require backup
recovery. Allow one writer per target.

The official acceptance suite checks the spec and successful/failed connections.
Its source-only suites are inapplicable. Native tests, CLI round trips and actual
Airbyte platform sync/replay tests validate writes and destination data.

See the repository README and `docs/releasing.md` for supported schemas, state
acknowledgements, failure/recovery behavior and release gates. This metadata is
for contribution/review; it does not declare Airbyte certification, support or
catalog/Cloud availability.
