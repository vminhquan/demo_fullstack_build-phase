FROM python:3.11-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY migrations ./migrations
COPY alembic.ini ./
COPY start.sh ./
RUN chmod +x start.sh

EXPOSE 8000
# Hosts such as Render inject PORT; docker compose keeps the default 8000.
CMD ["./start.sh"]
