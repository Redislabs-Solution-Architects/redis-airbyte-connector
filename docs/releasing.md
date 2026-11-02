# Publishing a release

The `Publish release` workflow builds and validates the Python distributions,
runs unit tests and installed-wheel smoke checks, then publishes:

- `redis-airbyte-connector` to PyPI using Trusted Publishing (`pypi` environment).
- Source and destination images to GHCR, each supporting Linux AMD64 and ARM64.
- Wheel, source distribution, and SHA-256 checksums to the GitHub release.

The Python package contains both `source-redis-adbc` and `destination-redis-adbc`.
The platform wheels and containers bundle the native library built from the
same pinned Redis ADBC revision. The published `adbc-driver-redis==0.0.19`
package lacks nested-struct ingestion required by this connector, so it cannot
replace that revision.

## One-time account setup

On PyPI, configure a pending or existing Trusted Publisher for:

- Project: `redis-airbyte-connector`
- Owner: `Redislabs-Solution-Architects`
- Repository: `redis-airbyte-connector`
- Workflow: `publish.yml`
- Environment: `pypi`

The workflow uses short-lived OIDC credentials; no PyPI API token is needed.
GitHub Actions needs `packages: write` for GHCR and `contents: write` to attach
release assets. After the first container publication, set both GHCR packages'
visibility to public and verify anonymous pulls. Container packages start private
unless visibility has already been configured.

## Release steps

1. Update `pyproject.toml`, Docker labels, README examples, CI examples and test
   script image versions together. Keep `requirements.lock` synchronized with
   runtime dependency changes.
2. Run unit tests, build and check distributions, and verify the installed CLIs
   outside the checkout. For native-driver changes, run the disposable TLS
   Enterprise integration tests and CLI round trips in the README.
3. Merge the tested change, then create a stable `vX.Y.Z` tag and publish its
   GitHub release. The tag must match the package version exactly.
4. Watch every publishing job to completion. Verify PyPI installation, image
   manifests for both architectures, and anonymous pulls. Record immutable image
   digests in release notes.

`workflow_dispatch` can publish an existing release tag. Set `publish_pypi` to
false for a container-only recovery run. PyPI versions cannot be overwritten;
after a partial release, rerun only failed jobs when possible. Use a new version
for package changes instead of replacing an existing published distribution.

## Native wheel builds

`scripts/build_native.py` stages the pinned native library and its license in
`source_redis_adbc/`. On Linux it uses the pinned Debian Bookworm Docker build
stage, establishing a glibc 2.36 baseline. On macOS ARM64 it uses Go 1.26.10 and
a macOS 12 deployment target. Release jobs build on native runners, set
`REDIS_WHEEL_PLATFORM` to the matching platform tag, and verify that the installed
library exports `AdbcDriverRedisInit` outside the checkout. The standalone ADBC
library does not depend on the CPython ABI, so these wheels use `py3-none` tags.

Source distributions omit generated libraries. To build a local native wheel,
install the Python build tool and run:

```sh
python scripts/build_native.py
python -m build --wheel
```

Linux needs Docker; macOS ARM64 needs Go and the Xcode command-line tools.
