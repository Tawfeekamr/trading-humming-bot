FROM hummingbot/hummingbot:latest

USER root

# Install our requirements into the conda hummingbot environment
COPY requirements.txt /tmp/requirements.txt
RUN /opt/conda/envs/hummingbot/bin/pip install --no-cache-dir -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt

# Copy our custom source modules
COPY src/ /home/hummingbot/src/

# Copy strategy script to hummingbot's scripts/ directory
COPY hummingbot_files/scripts/ta_grid_btcusdt.py /home/hummingbot/scripts/ta_grid_btcusdt.py

# Create v2 config so quickstart finds our script
RUN mkdir -p /home/hummingbot/conf/scripts
COPY config/ta_grid_btcusdt_conf.yml /home/hummingbot/conf/scripts/ta_grid_btcusdt.yml

# Ensure data/logs dirs exist
RUN mkdir -p /home/hummingbot/data /home/hummingbot/logs

# Fix ownership
RUN chown -R hummingbot:hummingbot /home/hummingbot/src \
    /home/hummingbot/scripts/ta_grid_btcusdt.py \
    /home/hummingbot/conf/scripts \
    /home/hummingbot/data \
    /home/hummingbot/logs

USER hummingbot

ENV SCRIPT_CONFIG=ta_grid_btcusdt.yml
ENV HEADLESS_MODE=true
ENV CONFIG_PASSWORD=

CMD ["/bin/bash", "-lc", "conda activate hummingbot && python ./bin/hummingbot_quickstart.py"]
