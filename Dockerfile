FROM python:3.11-slim

WORKDIR /app
COPY app.py memory_core.py /app/
COPY scripts/purge_expired.py /app/scripts/purge_expired.py
RUN useradd --system --uid 10001 chronicle && mkdir -p /app/data && chown -R chronicle:chronicle /app/data

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PORT=8000
ENV MEMORY_DB_PATH=/app/data/memories.sqlite3

USER chronicle
VOLUME ["/app/data"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)" || exit 1
CMD ["python", "app.py"]
