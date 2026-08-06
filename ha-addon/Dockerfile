# Flat-layout image for a packed HA add-on directory (see scripts/pack-addon).
# Build context = the packed folder (api.py, yhk_printer.py, etc. beside this file).
FROM python:3.12-slim-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends bluez \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY yhk_printer.py image_prep.py reddit_image.py api.py cat-printer.py markdown_renderer.py Lucon.ttf ./
COPY run.sh /run.sh
RUN chmod a+x /run.sh

ENV API_HOST=0.0.0.0
ENV API_PORT=8080
ENV PRINTER_FONT=/app/Lucon.ttf

EXPOSE 8080

CMD ["/run.sh"]
