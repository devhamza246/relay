FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 FASTEMBED_CACHE_PATH=/opt/models RELAY_CASSETTE=/app/eval/cassette.jsonl
WORKDIR /app
COPY pyproject.toml README.md ./
COPY app ./app
COPY eval ./eval
COPY scripts ./scripts
RUN pip install . \
 && python -c "from app.embed import Embedder; Embedder('BAAI/bge-small-en-v1.5').query('download the model at build time')" \
 && chmod -R a+rX /opt/models \
 && useradd --create-home --uid 10004 relay
ENV HF_HUB_OFFLINE=1
USER relay
EXPOSE 8000
CMD ["uvicorn", "app.api:get_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
