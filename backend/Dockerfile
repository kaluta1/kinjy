# Single Dockerfile shared by every Kaluta FastAPI service.
# Build with:  docker build --build-arg SERVICE=auth-service -f backend/Dockerfile backend
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DEFAULT_TIMEOUT=120

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

# Dependencies come BEFORE anything service-specific so this layer is built once
# and reused by all 13 services. Declaring ARG/ENV SERVICE above this line would
# give every service a different layer hash and re-run pip 13 times over.
COPY requirements.txt /tmp/requirements.txt
RUN pip install --retries 5 --timeout 120 -r /tmp/requirements.txt

# The Sonco Pay SDK, shipped with the code rather than fetched: it is not on a
# public index, and the version that pays real money should be the one that was
# tested, not whatever a registry serves on the day of a build. Pure Python and
# small, so it sits in the shared layer.
COPY vendor /tmp/vendor
RUN pip install --no-deps /tmp/vendor/*.whl

# Shared library, then the service itself.
COPY common /app/common

ARG SERVICE
ENV SERVICE=${SERVICE}

# media-service alone needs ffmpeg (video covers, short videos) and the voices
# that narrate short videos: Piper's natural voices, one per language, with
# espeak-ng as the declared fallback. All local: no vendor, no key.
# After the shared layers, so the other services' images are not touched.
RUN if [ "$SERVICE" = "media-service" ]; then \
        apt-get update && apt-get install -y --no-install-recommends ffmpeg espeak-ng curl \
        && rm -rf /var/lib/apt/lists/*; \
    fi
ARG PIPER_VOICES="en/en_US/ljspeech/high/en_US-ljspeech-high fr/fr_FR/siwis/medium/fr_FR-siwis-medium sw/sw_CD/lanfrica/medium/sw_CD-lanfrica-medium"
RUN if [ "$SERVICE" = "media-service" ]; then \
        pip install --retries 5 --timeout 120 "piper-tts==1.8.0" \
        && mkdir -p /opt/piper-voices && cd /opt/piper-voices \
        && for v in $PIPER_VOICES; do \
             n=$(basename "$v"); \
             curl -sfL --retry 5 -o "$n.onnx" "https://huggingface.co/rhasspy/piper-voices/resolve/main/$v.onnx" \
             && curl -sfL --retry 5 -o "$n.onnx.json" "https://huggingface.co/rhasspy/piper-voices/resolve/main/$v.onnx.json" \
             || exit 1; \
           done; \
    fi
# ai-service alone talks to a model vendor. Pinned to the 1.x line it was
# written against; installed here so the other images keep their layers.
RUN if [ "$SERVICE" = "ai-service" ]; then \
        pip install --retries 5 --timeout 120 "anthropic>=1.17,<2"; \
    fi
COPY ${SERVICE} /app

EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=25s --retries=5 \
    CMD curl -fsS http://localhost:8000/health || exit 1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
