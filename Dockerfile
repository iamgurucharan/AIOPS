FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_HOST=0.0.0.0 \
    APP_PORT=8080 \
    STATE_FILE=/data/state.json

WORKDIR /app
COPY server.py /app/server.py
COPY static /app/static
COPY data/incident_reference.json /app/data/incident_reference.json
RUN mkdir -p /data \
    && addgroup --system --gid 10001 app \
    && adduser --system --uid 10001 --ingroup app --home /nonexistent app \
    && chown 10001:10001 /data
USER 10001:10001
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 CMD python -c "from urllib.request import urlopen; urlopen('http://127.0.0.1:8080/healthz', timeout=2)"
CMD ["python", "/app/server.py"]
