# Assemble a self-contained HA local add-on folder from the single source tree.
# Output: dist/cat_printer/  ->  copy onto HAOS /addons/cat_printer
$Root = Split-Path -Parent $PSScriptRoot
$Out = Join-Path $Root "dist\cat_printer"

if (Test-Path $Out) { Remove-Item -Recurse -Force $Out }
New-Item -ItemType Directory -Force -Path $Out | Out-Null

Copy-Item (Join-Path $Root "yhk_printer.py") $Out
Copy-Item (Join-Path $Root "api.py") $Out
Copy-Item (Join-Path $Root "cat-printer.py") $Out
Copy-Item (Join-Path $Root "markdown_renderer.py") $Out
Copy-Item (Join-Path $Root "requirements.txt") $Out
Copy-Item (Join-Path $Root "Lucon.ttf") $Out
Copy-Item (Join-Path $Root "ha-addon\config.yaml") $Out
Copy-Item (Join-Path $Root "ha-addon\Dockerfile") $Out
Copy-Item (Join-Path $Root "ha-addon\run.sh") $Out

Write-Host "Packed add-on at $Out"
Write-Host "Copy that folder to Home Assistant /addons/cat_printer"
