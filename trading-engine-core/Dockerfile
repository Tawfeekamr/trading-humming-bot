# =============================================================
# Multi-stage Dockerfile for trading-engine-core
# Builds in CI (7GB RAM) — EC2 only pulls the final image
# =============================================================

# ---------- Stage 1: Build ----------
FROM rust:slim-bookworm AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    pkg-config \
    libssl-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# Copy manifests first for dependency layer caching
COPY Cargo.toml Cargo.lock ./

# Dummy lib.rs so cargo builds dependencies only (cached layer)
RUN mkdir src && echo "" > src/lib.rs
RUN cargo build --release 2>/dev/null || true

# Copy real source and rebuild
COPY src/ src/
RUN find src/ -type f -exec touch {} +
RUN cargo build --release

# ---------- Stage 2: Runtime ----------
FROM debian:bookworm-slim

# ca-certificates: required by rustls-tls-native-roots for Binance/Telegram TLS
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Non-root user
RUN groupadd -r bot && useradd -r -g bot -d /app -s /sbin/nologin bot

WORKDIR /app

COPY --from=builder /build/target/release/trading-bot /app/trading-bot

RUN mkdir -p /app/config /app/data && chown -R bot:bot /app

USER bot

# Config path defaults to config/strategy.yaml relative to CWD
ENTRYPOINT ["/app/trading-bot"]
CMD ["config/strategy.yaml"]
