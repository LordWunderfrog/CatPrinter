# Assemble a self-contained HA local add-on folder from the single source tree.
# Output: dist/cat_printer/
#   -Deploy  also mirrors that folder to the HA /addons share (default \\home.lan\addons\cat_printer)
param(
    [switch]$Deploy,
    [string]$DeployPath = $(if ($env:CAT_PRINTER_ADDON_DEPLOY) { $env:CAT_PRINTER_ADDON_DEPLOY } else { "\\home.lan\addons\cat_printer" })
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Out = Join-Path $Root "dist\cat_printer"

if (Test-Path $Out) { Remove-Item -Recurse -Force $Out }
New-Item -ItemType Directory -Force -Path $Out | Out-Null

Copy-Item (Join-Path $Root "yhk_printer.py") $Out
Copy-Item (Join-Path $Root "image_prep.py") $Out
Copy-Item (Join-Path $Root "api.py") $Out
Copy-Item (Join-Path $Root "cat-printer.py") $Out
Copy-Item (Join-Path $Root "reddit_image.py") $Out
Copy-Item (Join-Path $Root "markdown_renderer.py") $Out
Copy-Item (Join-Path $Root "requirements.txt") $Out
Copy-Item (Join-Path $Root "Lucon.ttf") $Out
Copy-Item (Join-Path $Root "ha-addon\config.yaml") $Out
Copy-Item (Join-Path $Root "ha-addon\Dockerfile") $Out
Copy-Item (Join-Path $Root "ha-addon\run.sh") $Out

Write-Host "Packed add-on at $Out"

if (-not $Deploy) {
    Write-Host "Copy that folder to Home Assistant /addons/cat_printer (or re-run with -Deploy)"
    exit 0
}

if (-not (Test-Path (Split-Path -Parent $DeployPath))) {
    throw "Deploy parent not reachable: $(Split-Path -Parent $DeployPath). Is the Samba share mounted?"
}

New-Item -ItemType Directory -Force -Path $DeployPath | Out-Null
robocopy $Out $DeployPath /MIR /NFL /NDL /NJH /NJS /nc /ns /np | Out-Null
$rc = $LASTEXITCODE
# robocopy: 0-7 = success-ish; >=8 = failure
if ($rc -ge 8) {
    throw "robocopy failed with exit code $rc (src=$Out dest=$DeployPath)"
}

Write-Host "Deployed to $DeployPath"
Write-Host "In HA: Settings → Add-ons → Cat Printer → Rebuild (then Start if stopped)."
