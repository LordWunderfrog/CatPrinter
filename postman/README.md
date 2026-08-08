# Postman

Collection **Cat Printer** in workspace **My Workspace** (personal).

| | |
|---|---|
| Collection UID | `21723956-35351089-2dae-4525-ba81-ff5e16a43201` |
| Environments | `Cat Printer — home.lan`, `Cat Printer — print.wunderfrog.com`, `Cat Printer — localhost` |

Open the Postman extension → sync/pull → select environment → **Health**, then **Print reddit default**.

Variables: `baseUrl`, `api_token` (secret, optional), `subreddit` (default `wunkus`).

Caller contract: [docs/CALLERS.md](../docs/CALLERS.md).  
Maintainer route notes: [docs/API.md](../docs/API.md).

Environments: use `home.lan` for LAN-only; `print.wunderfrog.com` for anything externally exposed or standalone; `localhost` for a local `python api.py`.

Auth: when the add-on token is set, send `X-Api-Key` on `/print/*` and `/printer/wake`. `/health` and `/status` stay open.
