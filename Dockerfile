FROM python:3.12.12-slim-bookworm@sha256:593bd06efe90efa80dc4eee3948be7c0fde4134606dd40d8dd8dbcade98e669c AS runtime
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    AIRBYTE_ENTRYPOINT="python -m source_redis_adbc"
WORKDIR /app
COPY requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock
COPY pyproject.toml README.md LICENSE ./
COPY source_redis_adbc ./source_redis_adbc
COPY destination_redis_adbc ./destination_redis_adbc
RUN pip install --no-cache-dir --no-build-isolation --no-deps . && useradd --uid 10001 --create-home connector
USER connector
LABEL io.airbyte.name=source-redis-adbc io.airbyte.version=0.1.1
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
LABEL io.airbyte.name=destination-redis-adbc io.airbyte.version=0.1.1
ENTRYPOINT ["python", "-m", "destination_redis_adbc"]

FROM production AS default-source
