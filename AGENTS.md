# Project instructions

- Always use the latest stable release of `redis/redis-adbc-driver`. Check the
  upstream latest release when updating dependencies or preparing a release.
- Pin the released commit and version in both `Dockerfile` and
  `scripts/build_native.py` for reproducible builds; keep these pins, the native
  integration version assertion and the README in sync. Meet the driver
  release's Go toolchain requirements in Docker, wheel builds and CI.
- Rebuild and validate the native artifacts after driver upgrades. Keep historical
  validation records tied to the revision actually tested.
