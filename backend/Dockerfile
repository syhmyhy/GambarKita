# GambarKita API: production image.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# libmagic enables the optional second file-type check (GK_LIBMAGIC=auto uses it on Linux).
RUN apt-get update \
    && apt-get install -y --no-install-recommends libmagic1 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 gambarkita

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY run.py .
COPY app ./app

# Photos and the SQLite database live in /data (a Docker volume).
RUN mkdir -p /data && chown gambarkita:gambarkita /data
USER gambarkita

ENV GK_ENVIRONMENT=production \
    GK_STORAGE_DIR=/data \
    GK_HOST=0.0.0.0 \
    GK_PORT=8000

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import sys, urllib.request; sys.exit(urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4).status != 200)"]

CMD ["python", "run.py"]
