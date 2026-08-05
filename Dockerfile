# Cat printer HTTP API. Needs Classic Bluetooth (BlueZ) + paired YHK printer.
# On HAOS this is meant as a custom add-on base (see ha-addon/).
FROM python:3.12-slim-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends bluez \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY yhk_printer.py api.py cat-printer.py ./
COPY Lucon.ttf ./
COPY images/ ./images/

ENV API_HOST=0.0.0.0
ENV API_PORT=8080
ENV PRINTER_FONT=/app/Lucon.ttf

EXPOSE 8080

CMD ["python", "api.py"]
