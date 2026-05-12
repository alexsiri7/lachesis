# Build with: docker build -f mcp/Dockerfile -t lachesis-mcp .
# (Build context must be the repo root.)
FROM python:3.12-slim

WORKDIR /app

COPY mcp/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY mcp/ /app/

ENV PYTHONPATH=/app
EXPOSE 8080

RUN adduser --disabled-password --gecos '' --uid 1000 appuser
USER appuser

CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080}"]
