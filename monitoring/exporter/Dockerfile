FROM python:3.12-slim@sha256:05cda9777409a9c3ffddd94a4c476b79f0769a0b4857f0c7ed9226b6800b0d6f

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    EXPORTER_DATA_DIR=/data \
    EXPORTER_PORT=9200

WORKDIR /opt/exporter
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY cicd_exporter ./cicd_exporter

RUN useradd --uid 10001 --no-create-home exporter && mkdir -p /data && chown exporter /data
USER exporter
VOLUME /data
EXPOSE 9200
HEALTHCHECK --interval=15s --timeout=3s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:9200/healthz', timeout=2)"

CMD ["python", "-m", "cicd_exporter"]
