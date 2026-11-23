FROM python:3.12-slim
RUN DEBIAN_FRONTEND=noninteractive apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --no-create-home --shell /usr/sbin/nologin tupa
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
