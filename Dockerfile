# The Gallery: production image (synthetic data, template writer, one sandbox per visitor)
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PUBLIC_DEMO=1 MODEL_PROVIDER=template LOG_JSON=1

WORKDIR /app
COPY requirements.lock requirements.txt ./
RUN pip install -r requirements.lock

COPY backend backend
COPY web web

# run as an unprivileged user; it only needs to write its own SQLite file
RUN useradd --create-home --uid 10001 gallery && chown -R gallery:gallery /app/backend/data
USER gallery

WORKDIR /app/backend
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import os,urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.getenv(\"PORT\",\"8000\")}/healthz', timeout=4)"
# Render, Azure and most hosts pass the port in $PORT
CMD ["sh", "-c", "exec uvicorn gallery.app:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*' --timeout-graceful-shutdown 10 --no-server-header --no-access-log"]
