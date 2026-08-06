# YHK-Cat-Thermal-Printer

Fork notes (Wunderfrog): library + HTTP API for Home Assistant / LAN use.

**One copy of the app code** — at the repo root. `ha-addon/` is only HA metadata (`config.yaml`, `run.sh`, flat `Dockerfile`). Pack when you deploy.

| Piece | Role |
|-------|------|
| `yhk_printer.py` | RFCOMM transport + protocol |
| `printer_service.py` | Lock, probe, wake, print recovery |
| `print_spool.py` | Disk spool under `/data/spool`; opportunistic drain |
| `image_prep.py` | Shared photo prep (EXIF, dither) |
| `cat-printer.py` | CLI smoke test |
| `reddit_image.py` | Random hot-image pick from a subreddit |
| `api.py` | HTTP API (`/health`, `/ready`, `/status`, `/printer/wake`, `/print/*`) |
| `ha/cat_printer.yaml` | HA package: poll status, bounded revive, give-up notify |
| `markdown_renderer.py` | Mistune AST → 384px 1-bit image |
| `Dockerfile` | Container build from repo root |
| `ha-addon/` | HAOS add-on metadata only |
| `scripts/pack-addon.*` | Builds `dist/cat_printer/`; `-Deploy` / `--deploy` syncs to HA `/addons` |

```bash
pip install -r requirements.txt
python cat-printer.py --text "hello"
python api.py   # http://0.0.0.0:8080
```

```bash
curl http://localhost:8080/health
curl http://localhost:8080/ready
curl -X POST http://localhost:8080/print/text -H "Content-Type: application/json" -d "{\"text\":\"hello\",\"font_size\":65}"
curl -X POST http://localhost:8080/print/markdown -H "Content-Type: application/json" -d "{\"markdown\":\"# List\\n\\n- milk\\n- eggs\"}"
curl -X POST http://localhost:8080/print/image -F "file=@images/Turtle.jpg"
curl -X POST http://localhost:8080/print/reddit -H "Content-Type: application/json" -d "{}"
```

PowerShell (preferred on Windows):

```powershell
Invoke-RestMethod http://localhost:8080/health
Invoke-RestMethod http://localhost:8080/ready
Invoke-RestMethod -Method Post -Uri http://localhost:8080/print/reddit -ContentType 'application/json' -Body (@{ subreddit = 'aww' } | ConvertTo-Json)
# If API_TOKEN is set:
Invoke-RestMethod -Method Post -Uri http://localhost:8080/print/reddit -ContentType 'application/json' -Headers @{ 'X-Api-Key' = 'your-token' } -Body '{}'
```

**Auth:** if `API_TOKEN` is set, `/print/*` and `/printer/wake` require `X-Api-Key` or `Authorization: Bearer …`. `/health`, `/ready`, and `/status` stay open (HA sensors). Leave token empty for open LAN during early bring-up. HA package: set `cat_printer_api_token` in `secrets.yaml` to the same value.

**Spool:** `/print/*` validates and prepares the image, writes it under `/data/spool` (survives rebuilds), then returns **202**. Drain runs on enqueue (if awake), `/status`/`/ready` awake, `/printer/wake` ok, startup, and every `SPOOL_RETRY_S` (default 2m) **only while jobs are parked**. No polling when empty. Spool full → 503. Depth + path on `/health`. Old jobs expire after `SPOOL_TTL_S` (default 7 days).

**Ready / status:** `GET /ready` probes RFCOMM — `printer: awake|busy` (200) or `sleepy` (503). `GET /status` is the same probe always as HTTP 200 (better for HA REST sensors).

**Wake:** `POST /printer/wake` — best-effort `bluetoothctl` disconnect/connect, then RFCOMM probe. Does **not** loop; HA owns attempt limits (see `ha/cat_printer.yaml`).

**HA revive package:** copy [`ha/cat_printer.yaml`](ha/cat_printer.yaml) to `/config/packages/`, enable `packages: !include_dir_named packages`, restart HA. Polls every 15m; up to 3 wakes; then one persistent notification + 6h cooldown. Not a substitute for pushing the cat’s button.

**Reddit** (`POST /print/reddit`): random printable pic via [Pullpush](https://pullpush.io/); default subreddit from `DEFAULT_SUBREDDIT` (HA option / env; default `wunkus`). Override with JSON `subreddit`.

**Markdown:** headings, paragraphs, bold/italic/strike, code, nested lists, task boxes, blockquotes, HR, tables, images (http/data/local; autocontrast+sharpen+Floyd–Steinberg dither), links as label + two-column end-of-paragraph QR (deduped), and ` ```qr ` fences. Text/QR stay hard-thresholded.

Env: `PRINTER_MAC`, `PRINTER_PORT`, `PRINTER_WIDTH`, `PRINTER_FONT`, `API_HOST`, `API_PORT`, `API_TOKEN`, `DEFAULT_SUBREDDIT`, `PRINTER_CONNECT_RETRIES`, `PRINTER_CONNECT_RETRY_DELAY`, plus optional ceilings `MAX_TEXT_CHARS`, `MAX_MARKDOWN_CHARS`, `MAX_UPLOAD_BYTES`, `MAX_IMAGE_PIXELS`, `MAX_RENDER_HEIGHT`.

HAOS deploy: `scripts/pack-addon.ps1 -Deploy` (or `pack-addon.sh --deploy`) packs and mirrors to `\\home.lan\addons\cat_printer` (override with `CAT_PRINTER_ADDON_DEPLOY`). Then **Rebuild** the add-on in HA. Set printer MAC / optional token / default subreddit in options, USB BT dongle on the HA VM, pair the printer, start the add-on. DNS/Caddy yourself (`print.wunderfrog.com` → HA `:8080`, LAN-only recommended).

---

Mini **cat/rabbit** **thermal** printer of the **YHK** type

<img src="https://raw.githubusercontent.com/abhigkar/YHK-Cat-Thermal-Printer/main/images/Cat-printer.jpeg"  width="300">
<img src="https://raw.githubusercontent.com/abhigkar/YHK-Cat-Thermal-Printer/main/images/default-test-print.png"  width="300">

This is yet another project with a **Cat/Rabbit thermal printer**. Other GitHub sources are also accessible, however none of them were successful for me because they all used the Cat-Printer with BLE protocol.

Unfortunately, my cat-printer uses a different firmware version that is based on the Classic bluetooth protocol rather than the GATT based protocol. **YHK-XXXX** was broadcast by my printer. The last four characters of the printer's **MAC address** are XXXX.

The Android and iOS app named **WalkPrint** is compatible with my cat printer. Although the app is worthless, some features need logging in.

My starting point: I was motivated from [This blogpost](https://werwolv.net/blog/cat_printerhttps:/) and planned to have my own printer. I did spent some time working on the [bitbank2/Thermal_Printer](https://github.com/bitbank2/Thermal_Printer) project, but I soon found that since my printer is different so no other code will run on it.

You can also read the full product review [here](https://hackspace.raspberrypi.com/articles/bluetooth-cat-thermal-printer-review)

Other reference projects [repositories](https://github.com/JJJollyjim/catprinter)

* [bitbank2/Thermal_Printer](https://github.com/bitbank2/Thermal_Printer)
* [WerWolv/PythonCatPrinter](https://github.com/WerWolv/PythonCatPrinter)
* [amber-sixel/PythonCatPrinter](https://github.com/amber-sixel/PythonCatPrinter)
* [the6p4c/catteprinter](https://github.com/the6p4c/catteprinter)
* [JJJollyjim/PyCatte](https://github.com/JJJollyjim/PyCatte)
* [xssfox](https://gist.github.com/xssfox/b911e0781a763d258d21262c5fdd2dec)

### Some real work of RE 🚀️

In order to obtain certain internals, I have started my own reverse engineering.

To examine the packet exchange between the phone and the printer, I decompiled the Android app, grabbed the BT snoop log from my phone, and then opened the log file in WireShark. And indeed, the BLE/GATT-based system was not the cause. Decompiled code for Android supports that.

### Action Replay 😄

Therefore, everything is straightforward. I attempted to send the same commands and data packets from my dependable Raspberry Pi Zero W through RFCOMM on the terminal based on the WirteShark logs. I finally succeeded in printing the identical image that was on my phone after a few failed attempts. In action replaySo things are simple. Based on the WirteShark logs, I tried to send the same commands/data paylods from my trusty **Raspberry pi Zero W** via **RFCOMM** on terminal. After few trial, I was able to print the same image as it was from my phone.

### The Final Result 👀️

To make this functional, the next task was to produce the data payload from my script. I went back and pulled three routines from the decompiled code to capture the BITMAP, transform it to 1 Bit pictures, and append some bytes as file headers. This step was more difficult because I was only able to obtain the function name. I then tried writing the similler routines in some other Python code, and it succeeded.

### How to use the script? 🎉️

1. Scan the MAC address of your printer using Bluetoothctl
2. Run scan on if printer found run pair xx:xx:xx:xx:xx:xx ADDR and trust xx:xx:xx:xx:xx:xx
3. Exit bluetoothctl
4. Run sdptool add --channel=N SP, where **"N"** is the channel, remember this as you will need this in the script. I have selected 2 in my case.
5. Run sudo rfcomm bind **N** xx:xx:xx:xx:xx:xx, N  = channel = port
6. Run cat-printer.py
7. 

### Notes: Usefull commands
sdptool add --channel=2 SP
sudo rfcomm connect /dev/rfcomm0 XX:XX:XX:XX:XX:XX 2
