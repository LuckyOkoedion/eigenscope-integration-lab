FROM python:3.12.13-slim-bookworm@sha256:4766d8b510c428e595d74b9cc5bbb2fae8e26316fffb4adc89908d79aacd58a2

LABEL org.opencontainers.image.source="https://github.com/LuckyOkoedion/eigenscope-integration-lab" \
      org.opencontainers.image.title="EigenScope Integration Lab" \
      org.opencontainers.image.licenses="Apache-2.0"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_NO_CACHE=1 \
    UV_PYTHON_DOWNLOADS=never \
    PATH="/app/.venv/bin:${PATH}"

RUN apt-get update && apt-get install --no-install-recommends -y git libgl1 libglfw3 ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir uv==0.13.6
WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN uv sync --frozen --all-extras --no-dev --no-editable --no-install-project
COPY src/ ./src/
RUN uv sync --frozen --all-extras --no-dev --no-editable \
    && useradd --system --uid 10001 --create-home lab \
    && mkdir -p /app/results && chown 10001:10001 /app/results
USER 10001:10001
EXPOSE 8765
HEALTHCHECK --interval=15s --timeout=3s --start-period=5s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/api/state', timeout=2).read()"]
ENTRYPOINT ["eigenscope-lab"]
CMD ["serve", "--host", "0.0.0.0", "--container", "--port", "8765"]
