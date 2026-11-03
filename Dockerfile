FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    TZ=Europe/Madrid

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates tzdata \
       libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b libharfbuzz-subset0 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# System libraries for headless Chromium (reading application forms); the browser itself is installed as `app` below.
RUN uv tool run --from playwright==1.64.1 playwright install-deps chromium && rm -rf /var/lib/apt/lists/* /root/.cache

RUN useradd --create-home --uid 1000 app
USER app
RUN mkdir -p /home/app/jobcopilot
WORKDIR /home/app/jobcopilot

# Claude Code CLI (official native installer) -> /home/app/.local/bin/claude
RUN curl -fsSL https://claude.ai/install.sh | bash
ENV PATH="/home/app/.local/bin:/home/app/jobcopilot/.venv/bin:${PATH}"

# Dependencies first for layer caching, then the project itself.
COPY --chown=app pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --extra browser --no-install-project
COPY --chown=app src ./src
RUN uv sync --frozen --no-dev --extra browser && playwright install chromium-headless-shell

VOLUME ["/data"]
ENTRYPOINT ["jobcopilot", "--home", "/data"]
CMD ["daemon"]
