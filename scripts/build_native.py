"""Build and stage the pinned standalone Redis ADBC library for a platform wheel."""

from pathlib import Path
import os
import platform
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
DRIVER_REF = "b99ec0aa3b61ed5db130211951d24191217efae8"


def run(*args, **kwargs):
    return subprocess.check_output(args, text=True, **kwargs).strip()


def main():
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
            environment = {**os.environ, "MACOSX_DEPLOYMENT_TARGET": "12.0", "GOTOOLCHAIN": "go1.26.10"}
            subprocess.run(["make", "build"], cwd=driver / "go", env=environment, check=True)
            shutil.copyfile(driver / "go/build/libadbc_driver_redis.dylib", destination / "libadbc_driver_redis.dylib")
            shutil.copyfile(driver / "LICENSE", destination / "native-LICENSE")
    else:
        raise SystemExit("Native release builds support Linux and macOS ARM64 only")


if __name__ == "__main__":
    main()
