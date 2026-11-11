"""Build and stage the pinned standalone Redis ADBC library for a platform wheel."""

from pathlib import Path
import os
import platform
import re
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
DRIVER_REF = "b052eedad9f2fb2c6eeb7c79fe63f8bbc3ca6bdd"
DRIVER_VERSION = "v0.0.24"


def run(*args, **kwargs):
    return subprocess.check_output(args, text=True, **kwargs).strip()


def main():
    docker_ref = re.search(r"^ARG REDIS_ADBC_REF=(\w+)$", (ROOT / "Dockerfile").read_text(), re.MULTILINE)
    assert docker_ref and docker_ref.group(1) == DRIVER_REF, "Native driver pins must match"
    docker_version = re.search(r"^ARG REDIS_ADBC_VERSION=(\S+)$", (ROOT / "Dockerfile").read_text(), re.MULTILINE)
    assert docker_version and docker_version.group(1) == DRIVER_VERSION, "Native driver versions must match"
    destination = ROOT / "source_redis_adbc"
    if platform.system() == "Linux":
        # Debian Bookworm fixes the Linux glibc build baseline at 2.36.
        subprocess.run(["docker", "build", "--target", "driver", "-t", "redis-airbyte-native", str(ROOT)], check=True)
        container = run("docker", "create", "redis-airbyte-native")
        try:
            subprocess.run(["docker", "cp", f"{container}:/driver/go/build/libadbc_driver_redis.so", str(destination)], check=True)
            subprocess.run(["docker", "cp", f"{container}:/driver/LICENSE", str(destination / "native-LICENSE")], check=True)
        finally:
            run("docker", "rm", container)
    elif platform.system() == "Darwin" and platform.machine() == "arm64":
        with tempfile.TemporaryDirectory(prefix="redis-airbyte-native-") as directory:
            driver = Path(directory)
            run("git", "init", directory)
            run("git", "-C", directory, "fetch", "--depth", "1", "https://github.com/redis/redis-adbc-driver.git", DRIVER_REF)
            run("git", "-C", directory, "checkout", "--detach", "FETCH_HEAD")
            environment = {**os.environ, "MACOSX_DEPLOYMENT_TARGET": "15.0", "GOTOOLCHAIN": "go1.27.3"}
            subprocess.run(["make", "build", f"VERSION={DRIVER_VERSION}"], cwd=driver / "go", env=environment, check=True)
            shutil.copyfile(driver / "go/build/libadbc_driver_redis.dylib", destination / "libadbc_driver_redis.dylib")
            shutil.copyfile(driver / "LICENSE", destination / "native-LICENSE")
    else:
        raise SystemExit("Native release builds support Linux and macOS ARM64 only")


if __name__ == "__main__":
    main()
