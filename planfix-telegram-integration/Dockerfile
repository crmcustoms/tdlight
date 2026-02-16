FROM python:3.11-slim

RUN apt-get update && apt-get install -y \
    libssl3 \
    zlib1g \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/

RUN mkdir -p /data/telegram /data

ENV TELEGRAM_DATABASE_DIR=/data/telegram
ENV DATABASE_URL=sqlite:////data/integration.db

EXPOSE 8000

CMD ["python", "-m", "src.main"]
