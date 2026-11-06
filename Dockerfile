FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml ./
COPY common ./common
COPY api ./api
COPY ingest ./ingest
COPY agent ./agent
COPY gateway ./gateway
COPY reconciler ./reconciler

RUN pip install --no-cache-dir .

CMD ["uvicorn", "api.app:app", "--host", "0.0.0.0", "--port", "8000"]
