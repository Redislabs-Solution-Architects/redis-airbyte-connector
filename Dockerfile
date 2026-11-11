# The driver and Python runtime are deliberately pinned independently of main.
FROM golang:1.27.3-bookworm@sha256:dc0963f32053e4bc451d24d87e3892d42d01021b61f093e98eb95c0352bb84b0 AS driver
ARG REDIS_ADBC_REF=b052eedad9f2fb2c6eeb7c79fe63f8bbc3ca6bdd
ARG REDIS_ADBC_VERSION=v0.0.24
WORKDIR /driver
RUN git init && git remote add origin https://github.com/redis/redis-adbc-driver.git \
    && git fetch --depth 1 origin ${REDIS_ADBC_REF} && git checkout --detach FETCH_HEAD
WORKDIR /driver/go
RUN make build VERSION=${REDIS_ADBC_VERSION}

FROM python:3.12.12-slim-bookworm@sha256:593bd06efe90efa80dc4eee3948be7c0fde4134606dd40d8dd8dbcade98e669c AS runtime
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    AIRBYTE_ENTRYPOINT="python -m source_redis_adbc"
WORKDIR /app
COPY --from=driver /driver/LICENSE /usr/share/licenses/redis-adbc-driver/LICENSE
COPY requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock
COPY pyproject.toml README.md LICENSE setup.py ./
COPY source_redis_adbc ./source_redis_adbc
COPY destination_redis_adbc ./destination_redis_adbc
COPY --from=driver /driver/go/build/libadbc_driver_redis.so ./source_redis_adbc/
COPY --from=driver /driver/LICENSE ./source_redis_adbc/native-LICENSE
RUN pip install --no-cache-dir --no-build-isolation --no-deps . && useradd --uid 10001 --create-home connector
USER connector
LABEL io.airbyte.name=ghcr.io/redislabs-solution-architects/source-redis-adbc io.airbyte.version=0.2.0
ENTRYPOINT ["python", "-m", "source_redis_adbc"]

FROM runtime AS test
USER root
RUN pip install --no-cache-dir 'pytest==9.2.0'
COPY tests ./tests
COPY scripts/integration_config.py ./scripts/integration_config.py
ENV PYTEST_ADDOPTS="-o cache_dir=/tmp/pytest-cache"
USER connector
ENTRYPOINT ["python", "-m", "pytest"]

FROM runtime AS production

FROM runtime AS destination
ENV AIRBYTE_ENTRYPOINT="python -m destination_redis_adbc"
LABEL io.airbyte.name=ghcr.io/redislabs-solution-architects/destination-redis-adbc io.airbyte.version=0.2.0
ENTRYPOINT ["python", "-m", "destination_redis_adbc"]

FROM production AS default-source
