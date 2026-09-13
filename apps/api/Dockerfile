FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.11.8@sha256:3b7b60a81d3c57ef471703e5c83fd4aaa33abcd403596fb22ab07db85ae91347 /uv /uvx /bin/

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev

COPY . .

EXPOSE 8000
CMD ["sh", "-c", "exec uv run --no-sync uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
