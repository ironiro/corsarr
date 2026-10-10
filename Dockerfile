FROM python:3.12-slim

# tzdata: log timestamps and "last watched" dates in local time (set TZ, e.g. Europe/Berlin)
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY corsarr ./corsarr

# Runs as an unprivileged user; /data holds database, logs and the GUI's config.json.
RUN useradd --uid 1000 --no-create-home --home-dir /data --shell /usr/sbin/nologin corsarr \
    && mkdir -p /data && chown corsarr:corsarr /data && chmod 700 /data
USER corsarr

# Release tag or commit the image was built from, and its update channel (set by CI) – the web
# interface compares them with GitHub
ARG CORSARR_VERSION=""
ARG CORSARR_CHANNEL=""
ENV CORSARR_VERSION=$CORSARR_VERSION \
    CORSARR_CHANNEL=$CORSARR_CHANNEL \
    DATA_DIR=/data \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Europe/Berlin
VOLUME ["/data"]
EXPOSE 8787

HEALTHCHECK --interval=60s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/health' % os.environ.get('WEBHOOK_PORT', '8787'), timeout=4)"

CMD ["python", "-m", "corsarr"]
