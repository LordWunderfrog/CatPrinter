# Cat printer HTTP API. Needs Classic Bluetooth (BlueZ) + paired YHK printer.
# Repo-root build: docker build -t cat-printer .
FROM python:3.12-slim-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends bluez \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY yhk_printer.py image_prep.py api.py cat-printer.py markdown_renderer.py Lucon.ttf ./
COPY ha-addon/run.sh /run.sh
RUN chmod a+x /run.sh

ENV API_HOST=0.0.0.0
ENV API_PORT=8080
ENV PRINTER_FONT=/app/Lucon.ttf

EXPOSE 8080

CMD ["/run.sh"]
