# Publishing a release

The `Publish release` workflow builds and validates the Python distributions,
runs unit tests and installed-wheel smoke checks, then publishes:

- `redis-airbyte-connector` to PyPI using Trusted Publishing (`pypi` environment).
- Source and destination images to GHCR, each supporting Linux AMD64 and ARM64.
- Wheel, source distribution, and SHA-256 checksums to the GitHub release.

The Python package contains both `source-redis-adbc` and `destination-redis-adbc`.
The pinned `adbc-driver-redis` dependency supplies the native library. Containers
use the same platform wheels as Python installations.

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
