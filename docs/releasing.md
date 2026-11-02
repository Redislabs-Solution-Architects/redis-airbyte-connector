# Publishing a release

Versioned source and destination containers are the primary Airbyte artifacts.
The Python distribution `redis-airbyte-connector` contains both CLI entry points
and native platform wheels. No new artifacts are published by pull-request CI.

## Required release gates

The reusable `Validate candidate artifacts` workflow is shared by PR validation
and publishing. It requires:

1. Unit/CLI tests, catalog metadata consistency, source-distribution build and
   package metadata checks.
2. One build of each connector image on native AMD64 and ARM64 runners. The
   images are saved with archive SHA-256 checksums and Docker image IDs.
3. Six authenticated TLS Redis Enterprise integration tests, Airbyte's official
   source/destination acceptance suites, PostgreSQL sync/replay and the Redis
   destination/source/destination round trip against those candidate images.
4. A disposable Airbyte platform: register both custom definitions, check both
   connectors, discover streams, run actual sync jobs, repeat Overwrite, and
   compare the final Redis data. Airbyte 1.8.5
   configurations omit unused empty secret objects to avoid its secret-hydration
   failure for an empty `database_options` object. The platform loads the saved AMD64 images.
5. For releases, install and load each native wheel outside the checkout.
   Linux wheels reuse the native library extracted from the tested images;
   macOS ARM64 builds the same pinned upstream revision.

Any failed gate blocks all registry publication. Candidate archives are verified
again after loading and before pushing. The publisher tags/pushes these images;
it never rebuilds them. Neither real credentials nor generated fixture
configurations are uploaded as artifacts.

## Start a release

Update the package version, Docker labels, both connector metadata files,
examples and image-version defaults together. Keep `requirements.lock` synchronized
with dependency changes. Merge the passing PR, then push a stable tag matching
`pyproject.toml`:

```sh
git tag -a vX.Y.Z -m 'Release X.Y.Z'
git push origin vX.Y.Z
```

The publishing workflow verifies the tag and that its commit belongs to `main`,
then creates or retains a **draft** GitHub release. After every gate passes it:

- Publishes immutable Python distributions using PyPI Trusted Publishing.
- Pushes the tested architecture images and creates the versioned manifests.
- Attaches the distributions and checksums without overwriting different bytes.
- Checks public PyPI hashes and compares both registry architectures' image IDs
  with their tested identities.
- Promotes `latest` only for a non-older release and publishes the GitHub draft.

PyPI and GHCR do not offer a shared atomic transaction. A partial upload may be
visible while the release remains a draft. Failed jobs must be fixed/retried
before announcing completion. Rerun failed jobs to retain the original candidate
artifacts; do not rebuild and overwrite an existing version. Hash/identity
mismatches require a new version.

The existing PyPI publisher remains scoped to repository
`Redislabs-Solution-Architects/redis-airbyte-connector`, workflow `publish.yml`,
and environment `pypi`. It uses OIDC and stores no PyPI API token.

## Validate without publishing

A manual `Publish release` run defaults to `publish=false`. Leave `tag` empty to
validate the selected ref, including all native wheels, without creating a release
or changing PyPI/GHCR:

```sh
gh workflow run publish.yml --ref YOUR_BRANCH -f publish=false
```

To resume a tagged draft release, dispatch its existing tag with `publish=true`.
An already public release cannot be silently republished.

## Local checks

```sh
python scripts/check_metadata.py
python scripts/enterprise_env.py up
python scripts/enterprise_env.py test --source-image redis-airbyte/source-redis-adbc:tested
python scripts/acceptance_test.py --source-image redis-airbyte/source-redis-adbc:tested --destination-image redis-airbyte/destination-redis-adbc:tested
python scripts/enterprise_env.py e2e --source-image redis-airbyte/source-redis-adbc:tested --destination-image redis-airbyte/destination-redis-adbc:tested
python scripts/platform_env.py up
python scripts/platform_test.py --source-image redis-airbyte/source-redis-adbc:tested --destination-image redis-airbyte/destination-redis-adbc:tested
python scripts/platform_env.py down
python scripts/enterprise_env.py down
```

Build the two `:tested` images first using Docker targets `runtime` and
`destination`. The platform helper pins abctl 0.30.4 and Airbyte chart/application
1.8.5. It refuses to replace an existing local Airbyte installation and records
the node ID before permitting cleanup. It requires Docker, network access and
sufficient memory/disk for the platform and Enterprise fixture.

## Acceptance coverage

The pinned official CAT 2.0.1 image runs its unchanged tests through its Dagger
0.6.4 engine on the isolated fixture network. The source uses high strictness
with expected records and repeat full-refresh checks. Incremental tests are
inapplicable. Destination source-only suites are inapplicable; native and platform
tests cover destination writes instead. Historical-image compatibility tests are
explicitly deselected: CAT's global local-image import setting would otherwise
compare the candidate with itself. This suite does not claim historical-image
backward-compatibility coverage.

## Catalog contribution

Both `connectors/*/` directories contain stable definition IDs, catalog metadata,
icons and acceptance configuration. Registry inclusion remains disabled until
Airbyte maintainers review and accept the contribution. The source proposal is
[airbytehq/airbyte#90323](https://github.com/airbytehq/airbyte/pull/90323); avoid
opening a duplicate source PR. A destination proposal and catalog/Cloud enablement
require upstream review. Publishing our independent package does not grant an
Airbyte support level or catalog listing.

## Native builds

The native revision remains pinned to
`b99ec0aa3b61ed5db130211951d24191217efae8`. The public
`adbc-driver-redis==0.0.19` wheel lacks this destination's nested-struct ingestion.
Linux uses Debian Bookworm (glibc 2.36+); macOS ARM64 uses a macOS 12 target.
Source distributions require a separate native build; platform wheels include it.
