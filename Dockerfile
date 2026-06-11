FROM python:3.12-slim

RUN pip install --no-cache-dir uv

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
COPY lightclaw/ ./lightclaw/

RUN uv pip install --system --no-cache .

RUN useradd -m lightclaw
USER lightclaw

ENV PYTHONUNBUFFERED=1

CMD ["lightclaw", "discord"]
