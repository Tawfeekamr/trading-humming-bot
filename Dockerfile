FROM hummingbot/hummingbot:latest

WORKDIR /home/hummingbot

# Install additional Python packages needed by our strategy
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt && rm /tmp/requirements.txt

# Copy only our custom code — do NOT overwrite the entire /home/hummingbot/
# or the hummingbot source gets wiped out
COPY src/ /home/hummingbot/src/
COPY hummingbot_files/ /home/hummingbot/hummingbot_files/
COPY config/ /home/hummingbot/config/

# Ensure data/logs dirs exist with correct ownership
USER root
RUN mkdir -p /home/hummingbot/data /home/hummingbot/logs \
    && chown -R hummingbot:hummingbot /home/hummingbot/data /home/hummingbot/logs /home/hummingbot/src /home/hummingbot/hummingbot_files /home/hummingbot/config
USER hummingbot

CMD ["start", "--script", "hummingbot_files/scripts/ta_grid_btcusdt.py"]
