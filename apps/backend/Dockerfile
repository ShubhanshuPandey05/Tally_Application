# syntax=docker/dockerfile:1

# Build stage: compile wheels once so the runtime image needs no toolchain.
FROM python:3.12-slim AS builder

WORKDIR /build
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential \
 && rm -rf /var/lib/apt/lists/*

COPY packages/tally_core /build/packages/tally_core
COPY apps/backend /build/apps/backend

RUN python -m pip install --no-cache-dir --upgrade pip \
 && python -m pip wheel --no-cache-dir --wheel-dir /wheels \
      /build/packages/tally_core /build/apps/backend


FROM python:3.12-slim AS runtime

# Never run as root: a request-handling process should not be able to modify
# its own code, and the container needs nothing outside its virtualenv.
RUN useradd --create-home --uid 10001 tallyflow

WORKDIR /app
COPY --from=builder /wheels /wheels
RUN python -m pip install --no-cache-dir /wheels/*.whl && rm -rf /wheels

USER tallyflow

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

EXPOSE 8000

# Liveness only -- readiness (/v1/ready) checks the database, and a database
# blip must not restart every replica and turn a partial outage into a total one.
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/v1/health',timeout=2).status==200 else 1)"

# One worker per container. Connectors hold long-lived WebSockets to a specific
# process, so scaling means more containers plus TALLYFLOW_REDIS_URL -- forking
# workers inside one container would give each fork a different set of sockets
# and no way to route between them.
CMD ["uvicorn", "tally_backend.main:create_app", "--factory", \
     "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "*", \
     "--timeout-graceful-shutdown", "30"]
