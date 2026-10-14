# --- css: Tailwind standalone CLI (no Node) scans the templates and builds app.css -------
FROM python:3.13-slim AS css
ARG TAILWIND_VERSION=v4.3.3
ARG TARGETARCH
WORKDIR /src
RUN arch=$([ "$TARGETARCH" = arm64 ] && echo arm64 || echo x64) \
    && python -c "import sys, urllib.request; urllib.request.urlretrieve(sys.argv[1], '/usr/local/bin/tailwindcss')" \
       "https://github.com/tailwindlabs/tailwindcss/releases/download/${TAILWIND_VERSION}/tailwindcss-linux-${arch}" \
    && chmod +x /usr/local/bin/tailwindcss
COPY backend/assets backend/assets
COPY backend/templates backend/templates
COPY backend/static backend/static
RUN tailwindcss -i backend/assets/app.css -o backend/static/app.css --minify

# --- app ----------------------------------------------------------------------------------
FROM python:3.13-slim

WORKDIR /app

# libzbar0: QR decoding of the receipt's fiscal code (pyzbar)
RUN apt-get update && apt-get install -y --no-install-recommends libzbar0 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# uid 1000 = `worker` on the docker host, which owns the bind-mounted ./data
RUN useradd --uid 1000 --user-group --no-create-home --shell /usr/sbin/nologin app \
    && mkdir -p /app/data && chown app:app /app/data

COPY backend/ backend/
COPY --from=css /src/backend/static/app.css backend/static/app.css

ENV RECEIPTS_DATA=/app/data TZ=Europe/Sofia
EXPOSE 8000
USER 1000:1000

# Migrations run at startup (app lifespan). One worker: the reader thread runs in-process.
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
