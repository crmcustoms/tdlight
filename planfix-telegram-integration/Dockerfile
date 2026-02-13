# Stage 1: Build TDLight (https://github.com/tdlight-team/tdlight)
FROM ubuntu:22.04 AS builder

RUN apt-get update && apt-get install -y \
    build-essential \
    cmake \
    gperf \
    libssl-dev \
    zlib1g-dev \
    git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build/tdlight
COPY . /build/tdlight/

RUN mkdir -p build && cd build && \
    cmake -DCMAKE_BUILD_TYPE=Release \
          -DCMAKE_INSTALL_PREFIX=/usr/local \
          .. && \
    cmake --build . --target tdjson -j$(nproc) && \
    cmake --install .

# Stage 2: Python runtime with TDLight
FROM python:3.11-slim

RUN apt-get update && apt-get install -y \
    libssl3 \
    zlib1g \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /usr/local/lib/libtdjson* /usr/local/lib/
RUN ldconfig

WORKDIR /app

COPY planfix-telegram-integration/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY planfix-telegram-integration/src/ ./src/

RUN mkdir -p /data/telegram /data

ENV TDJSON_PATH=/usr/local/lib/libtdjson.so
ENV TELEGRAM_DATABASE_DIR=/data/telegram
ENV DATABASE_URL=sqlite:////data/integration.db

EXPOSE 8000

CMD ["python", "-m", "src.main"]
