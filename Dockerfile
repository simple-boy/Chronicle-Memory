FROM python:3.11-slim

WORKDIR /app
COPY . /app

ENV PYTHONUNBUFFERED=1
ENV PORT=8000
ENV MEMORY_DB_PATH=/app/data/memories.sqlite3

EXPOSE 8000
CMD ["python", "app.py"]
