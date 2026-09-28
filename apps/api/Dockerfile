FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.11.8@sha256:3b7b60a81d3c57ef471703e5c83fd4aaa33abcd403596fb22ab07db85ae91347 /uv /uvx /bin/

# The API and scheduled workers use the same runtime. Chromium is installed at
# build time and only launched by scraper jobs; the API keeps its own process.
ENV PLAYWRIGHT_BROWSERS_PATH=/app/playwright-browsers

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev

RUN uv run --no-sync playwright install --with-deps chromium

# Copy runtime paths explicitly; never overlay the image's .venv from the host.
COPY main.py ./
COPY src/ ./src/
COPY scripts/ ./scripts/
COPY migrations/ ./migrations/

EXPOSE 8000
CMD ["sh", "-c", "exec uv run --no-sync uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
