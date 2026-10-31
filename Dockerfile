FROM python:3.12-slim
WORKDIR /srv
ENV TIKTOKEN_CACHE_DIR=/srv/.tiktoken
COPY requirements.lock .
RUN pip install --no-cache-dir -r requirements.lock
# Tokenizadores precargados: evita descargarlos en la primera petición (el chunking y el conteo de tokens los usan).
RUN python -c "import tiktoken; tiktoken.get_encoding('cl100k_base'); tiktoken.get_encoding('o200k_base')"
COPY app ./app
COPY data ./data
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
