# Flight snapshot qualification — 2026-11-17

The requester asked to complete redis/redis-apache-arrow-flight#65. This change
adds an independent Airbyte source lifecycle implementation of the gateway's
protocol-1 extension. It uses the Python ADBC Flight SQL driver only; no native
Redis shared library is loaded by the Flight integration tests.

The actual source check/discover/read path acquires before read-time rediscovery
and schema validation. Two tables and a view retain duplicate multiplicities,
NULLs, Unicode and exact 64-bit integer conversion while another connection
updates, changes schema, truncates and drops the live view. An additional actual
generator-close case asserts release before connection close. Unit coverage
rejects unsupported protocol, plaintext and unsafe session/header/TLS overrides.

Commands (connection credentials and CA are supplied through the environment):

```sh
python -m pytest -q tests -m 'not integration'
python -m pytest -v --tb=short tests/test_flight_snapshot_integration.py
```

Final source base: `3b110b0d4ac3b5134b028d8e9185da3134abba7c`, incorporating
the concurrent dependency update. Python 3.12.15, CDK 7.49.0, Python ADBC Flight
SQL/manager 1.13.0 and PyArrow 26.0.1 were installed from this repository's own
lock. The gateway used Go 1.27.4, Arrow Go 18.9.0, ADBC Go 1.13.0, gRPC 1.85.0
and released Redis ADBC v0.0.28 (`b63a010d88d2b69707594cb9dc063992dac06b32`).

The unit command passed **194 tests** (12 integration cases deselected; three CDK
ExperimentalClass warnings). The independent Flight command passed **both tests**
in 12.60 seconds, with verified TLS and Redis ACL credentials on both hops. The
backend was disposable Redis Software 8.2, Redis 8.6.2, Search, three active
primaries, verified active 4x QPF and one managed catalog/hash slot. The actual
Flight tests do not load the bundled native Redis library. They do not repeat or
supersede the upstream native-artifact qualification accompanying the dependency
refresh.

The paired gateway [qualification receipt](https://github.com/redis/redis-apache-arrow-flight/blob/main/docs/validation/2026-11-17-snapshots/README.md)
records complete public/private composition fingerprints, backend/race results
and independent fault/security checks, including real lease loss, fixed expiry,
compaction, SQL/Redis ACL revocation, disconnect and SIGKILL/restart cleanup.
Earlier native-only records remain historical; published connector releases do
not gain Flight snapshot support until this change is accepted and released.
No incremental mode, independent-shard consistency or durable resume is added.
