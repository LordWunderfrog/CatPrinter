# Home Assistant OS local add-on build context is this directory.
FROM python:3.12-slim-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends bluez \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY yhk_printer.py api.py cat-printer.py ./
COPY Lucon.ttf ./
COPY run.sh /
RUN chmod a+x /run.sh

ENV PRINTER_FONT=/app/Lucon.ttf

CMD ["/run.sh"]
