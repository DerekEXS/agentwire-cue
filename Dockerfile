# AgentWire-Cue v1.5.3 — plugin host (statechart + admin + a2a triggers).
#
# Pairs with agentwire-core. The container exposes 18801 (A2A inbound
# listener) + 19000 (admin API). Healthcheck runs ``agentwire-cue
# doctor --no-network`` so the orchestrator gets a green tick once
# token + port + plugin deps look healthy, without depending on the
# downstream CORE being reachable.
FROM python:3.13-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# Copy the whole package into a subdirectory whose name matches the
# importable package — ``python -m agentwire_cue`` then works.
COPY __init__.py __main__.py /app/agentwire_cue/
COPY core/ /app/agentwire_cue/core/
COPY schema/ /app/agentwire_cue/schema/

# Default empty plugin dir + data dir. Operators bind-mount real
# plugins at /plugins. /data holds persisted state.
RUN groupadd -r agentwire \
    && useradd -r -g agentwire -m -d /home/agentwire agentwire \
    && mkdir -p /plugins /data \
    && chown -R agentwire:agentwire /app /plugins /data

USER agentwire

# A2A listener + admin API.
EXPOSE 18801 19000

VOLUME ["/plugins", "/data"]

# Default plumbing for compose. CUE_CORE_URL points at the companion
# CORE service; admin token resolved from the mounted secret.
ENV PYTHONPATH=/app \
    CUE_CORE_URL=http://agentwire-core:18800 \
    CUE_DOCTOR_A2A_URL=http://agentwire-core:18800

HEALTHCHECK --interval=30s --timeout=10s --retries=3 --start-period=10s \
    CMD python3 -m agentwire_cue doctor \
        --no-network \
        --a2a-listener-port 18801 \
        --admin-port 19000 \
        || exit 1

CMD ["python3", "-m", "agentwire_cue", "host", \
     "--plugin-dir", "/plugins", \
     "--a2a-url", "http://agentwire-core:18800", \
     "--a2a-token-file", "/run/secrets/a2a-token.txt", \
     "--admin-token-file", "/run/secrets/cue-admin-token.txt", \
     "--admin-host", "0.0.0.0", \
     "--admin-port", "19000", \
     "--a2a-listener-host", "0.0.0.0", \
     "--a2a-listener-port", "18801"]
