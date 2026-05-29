# Stage 1: Build Rust wheel
FROM rust:1.82-slim AS rust-builder
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-pip python3-venv && rm -rf /var/lib/apt/lists/*
RUN python3 -m pip install --no-cache-dir --break-system-packages maturin
COPY trading-engine-core/ /build/trading-engine-core/
WORKDIR /build/trading-engine-core
RUN maturin build --release --out /build/wheels
# The wheel is in /build/wheels/

# Stage 2: Main hummingbot image
FROM hummingbot/hummingbot:version-2.13.0

# Install curl for Telegram API calls
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

# Install our requirements into the conda hummingbot environment
COPY requirements.txt /tmp/requirements.txt
RUN /opt/conda/envs/hummingbot/bin/pip install --no-cache-dir -r /tmp/requirements.txt && rm /tmp/requirements.txt

# Install trading-engine-core Rust wheel (built in Stage 1)
COPY --from=rust-builder /build/wheels/*.whl /tmp/wheels/
RUN /opt/conda/envs/hummingbot/bin/pip install --no-cache-dir /tmp/wheels/trading_engine_core*.whl && rm -rf /tmp/wheels

# Copy our custom source modules
COPY src/ /home/hummingbot/src/

# Copy strategy scripts to hummingbot's scripts/ directory
COPY hummingbot_files/scripts/ta_grid_btcusdt.py /home/hummingbot/scripts/ta_grid_btcusdt.py
COPY hummingbot_files/scripts/ta_grid_trend.py /home/hummingbot/scripts/ta_grid_trend.py
COPY hummingbot_files/scripts/pair_engine.py /home/hummingbot/scripts/pair_engine.py
COPY hummingbot_files/scripts/capital_manager.py /home/hummingbot/scripts/capital_manager.py

# Copy diagnostic scripts
COPY scripts/diagnose_telegram.py /home/hummingbot/scripts/diagnose_telegram.py

# Create v2 config so quickstart finds our script
RUN mkdir -p /home/hummingbot/conf/scripts
COPY config/ta_grid_btcusdt_conf.yml /home/hummingbot/conf/scripts/ta_grid_btcusdt.yml
COPY hummingbot_files/conf/scripts/ta_grid_trend_conf.yml /home/hummingbot/conf/scripts/ta_grid_trend_conf.yml

# Ensure data/logs/models dirs exist
RUN mkdir -p /home/hummingbot/data /home/hummingbot/logs /home/hummingbot/models

# Copy pre-trained ML model
COPY models/ /home/hummingbot/models/

# Copy entrypoint script
COPY docker-entrypoint.sh /home/hummingbot/docker-entrypoint.sh
RUN chmod +x /home/hummingbot/docker-entrypoint.sh

# Trading engine enabled (Rust indicators + StrategyHost)
ENV USE_TRADING_ENGINE=true

ENV SCRIPT_CONFIG=ta_grid_trend_conf.yml
ENV HEADLESS_MODE=true

CMD ["/home/hummingbot/docker-entrypoint.sh"]
