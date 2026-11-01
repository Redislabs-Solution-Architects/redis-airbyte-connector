import signal
import sys

from source_redis_adbc.__main__ import _terminate

from .destination import DestinationRedisAdbc


def main():
    signal.signal(signal.SIGTERM, _terminate)
    DestinationRedisAdbc().run(sys.argv[1:])


if __name__ == "__main__":
    main()
