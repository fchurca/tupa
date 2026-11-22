FROM python:3.12-slim
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --home /app/src --no-create-home --shell /usr/sbin/nologin tupa \
    && mkdir -p /app/src \
    && chown -R tupa:tupa /app
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TUPA_HOST=0.0.0.0 \
    TUPA_PORT=8000
WORKDIR /app/src
COPY --chown=tupa:tupa src/ ./
COPY --chown=tupa:tupa pyproject.toml /app/pyproject.toml
USER tupa
EXPOSE 8000
CMD ["python", "-m", "tupa", "--serve"]
