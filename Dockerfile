# The driver and Python runtime are deliberately pinned independently of main.
FROM golang:1.26.10-bookworm@sha256:23ce71337b726608afb1bc00986e630a05034a87a87ea1fc2cd1ae5cf33387eb AS driver
ARG REDIS_ADBC_REF=b99ec0aa3b61ed5db130211951d24191217efae8
WORKDIR /driver
RUN git init && git remote add origin https://github.com/redis/redis-adbc-driver.git \
    && git fetch --depth 1 origin ${REDIS_ADBC_REF} && git checkout --detach FETCH_HEAD
WORKDIR /driver/go
RUN make build

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
LABEL io.airbyte.name=ghcr.io/redislabs-solution-architects/source-redis-adbc io.airbyte.version=0.1.3
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
LABEL io.airbyte.name=ghcr.io/redislabs-solution-architects/destination-redis-adbc io.airbyte.version=0.1.3
ENTRYPOINT ["python", "-m", "destination_redis_adbc"]

FROM production AS default-source
