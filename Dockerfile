FROM hummingbot/hummingbot:latest

WORKDIR /home/hummingbot

COPY requirements.txt /home/hummingbot/
RUN pip install --no-cache-dir -r requirements.txt

COPY . /home/hummingbot/

RUN mkdir -p data logs

USER root
RUN chown -R hummingbot:hummingbot /home/hummingbot
USER hummingbot

CMD ["start", "--script", "hummingbot_files/scripts/ta_grid_btcusdt.py"]
