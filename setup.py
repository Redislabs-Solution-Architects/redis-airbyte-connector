"""Mark wheels containing the standalone ADBC library as platform-specific."""

from pathlib import Path
import os

from setuptools import Distribution, setup
from setuptools.command.bdist_wheel import bdist_wheel


class NativeDistribution(Distribution):
    def has_ext_modules(self):
        return any(Path("source_redis_adbc").glob("libadbc_driver_redis.*"))


class NativeWheel(bdist_wheel):
    def get_tag(self):
        if self.distribution.has_ext_modules():
            # This is a standalone C ADBC library, independent of CPython's ABI.
            return "py3", "none", os.environ.get("REDIS_WHEEL_PLATFORM", super().get_tag()[2])
        return super().get_tag()


setup(distclass=NativeDistribution, cmdclass={"bdist_wheel": NativeWheel})
