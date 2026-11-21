FROM python:3.12-slim
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TUPA_HOST=0.0.0.0 \
    TUPA_PORT=8000
WORKDIR /app
COPY tupa/ ./tupa/
EXPOSE 8000
CMD ["python", "-m", "tupa", "--serve"]
