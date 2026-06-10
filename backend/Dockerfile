FROM python:3.12-slim

# gzip/pigz: compress dump streams piped from docker exec
# tar: volume backup/restore via alpine helper containers
# curl: healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends \
    pigz \
    gzip \
    tar \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/

HEALTHCHECK --interval=30s --timeout=5s \
  CMD curl -f http://localhost:7731/api/docs || exit 1

EXPOSE 7731

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "7731", "--workers", "1", "--proxy-headers", "--forwarded-allow-ips=*"]
