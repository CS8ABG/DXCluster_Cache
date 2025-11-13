# syntax=docker/dockerfile:1
FROM python:3.11-slim

LABEL maintainer="Bruno (CS8ABG)"
LABEL description="DXCluster Cache - DX Cluster Spot Cache with Web Portal"

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

ENV WEB_PORT=8000

EXPOSE ${WEB_PORT}

CMD ["bash", "-c", "python3 dxcluster_cache.py --port ${WEB_PORT}"]
