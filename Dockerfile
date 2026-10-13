# The Gallery: public demo image (synthetic data, template writer, one sandbox per visitor)
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    PUBLIC_DEMO=1 MODEL_PROVIDER=template

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY backend backend
COPY web web

WORKDIR /app/backend
EXPOSE 8000
# Render, Azure and most hosts pass the port in $PORT
CMD ["sh", "-c", "uvicorn gallery.app:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
