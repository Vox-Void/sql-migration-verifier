FROM eclipse-temurin:17.0.18_8-jre-jammy@sha256:642d45bf22d3cb9face159181732ed9fa70873b2681e50445eff7d4785c176bb AS java
FROM ghcr.io/astral-sh/uv:0.13.9 AS uv
FROM python:3.11.15-slim-bookworm@sha256:d29f48a31a8b408ed19272ca1e7b10ebae13b240a27e862d3d4217c528e2e0c3

COPY --from=java /opt/java/openjdk /opt/java/openjdk
COPY --from=uv /uv /usr/local/bin/uv
ENV JAVA_HOME=/opt/java/openjdk \
    PATH=/app/.venv/bin:/opt/java/openjdk/bin:$PATH \
    PYTHONPATH=/app/src \
    PYTHONDONTWRITEBYTECODE=1 \
    SPARK_LOCAL_IP=127.0.0.1 \
    PYSPARK_SUBMIT_ARGS="--driver-memory 768m pyspark-shell" \
    JAVA_TOOL_OPTIONS="-Duser.timezone=UTC -XX:ActiveProcessorCount=2" \
    TZ=UTC
WORKDIR /app
COPY pyproject.toml uv.lock ./
# Install the locked worker dependencies and create the Spark user.
RUN uv sync --locked --only-group worker --python /usr/local/bin/python \
    && groupadd --gid 10001 spark \
    && useradd --uid 10001 --gid 10001 --no-create-home spark
COPY src/spark_worker src/spark_worker
USER spark
ENTRYPOINT ["python", "-m", "spark_worker.worker"]
