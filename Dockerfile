FROM hummingbot/hummingbot:version-2.13.0

# Install curl for Telegram API calls
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

# Install our requirements into the conda hummingbot environment
COPY requirements.txt /tmp/requirements.txt
RUN /opt/conda/envs/hummingbot/bin/pip install --no-cache-dir -r /tmp/requirements.txt && rm /tmp/requirements.txt

# Copy our custom source modules
COPY src/ /home/hummingbot/src/

# Copy strategy scripts to hummingbot's scripts/ directory
COPY hummingbot_files/scripts/ta_grid_btcusdt.py /home/hummingbot/scripts/ta_grid_btcusdt.py
COPY hummingbot_files/scripts/ta_grid_trend.py /home/hummingbot/scripts/ta_grid_trend.py

# Copy diagnostic scripts
COPY scripts/diagnose_telegram.py /home/hummingbot/scripts/diagnose_telegram.py

# Create v2 config so quickstart finds our script
RUN mkdir -p /home/hummingbot/conf/scripts
COPY config/ta_grid_btcusdt_conf.yml /home/hummingbot/conf/scripts/ta_grid_btcusdt.yml
COPY hummingbot_files/conf/scripts/ta_grid_trend_conf.yml /home/hummingbot/conf/scripts/ta_grid_trend_conf.yml

# Ensure data/logs dirs exist
RUN mkdir -p /home/hummingbot/data /home/hummingbot/logs

# Copy entrypoint script
COPY docker-entrypoint.sh /home/hummingbot/docker-entrypoint.sh
RUN chmod +x /home/hummingbot/docker-entrypoint.sh

ENV SCRIPT_CONFIG=ta_grid_trend_conf.yml
ENV HEADLESS_MODE=true

CMD ["/home/hummingbot/docker-entrypoint.sh"]
