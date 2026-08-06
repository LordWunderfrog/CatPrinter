# Assemble a self-contained HA local add-on folder from the single source tree.
# Output: dist/cat_printer/
#   -Deploy  also mirrors that folder to the HA /addons share (default \\home.lan\addons\cat_printer)
# File list: scripts/addon-files.txt (shared with pack-addon.sh)
param(
    [switch]$Deploy,
    [string]$DeployPath = $(if ($env:CAT_PRINTER_ADDON_DEPLOY) { $env:CAT_PRINTER_ADDON_DEPLOY } else { "\\home.lan\addons\cat_printer" })
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Out = Join-Path $Root "dist\cat_printer"
$Manifest = Join-Path $PSScriptRoot "addon-files.txt"

if (-not (Test-Path $Manifest)) {
    throw "Missing pack manifest: $Manifest"
}

if (Test-Path $Out) { Remove-Item -Recurse -Force $Out }
New-Item -ItemType Directory -Force -Path $Out | Out-Null

Get-Content $Manifest | ForEach-Object {
    $line = $_.Trim()
    if (-not $line -or $line.StartsWith("#")) { return }
    $parts = $line.Split("|", 2)
    $srcRel = $parts[0].Trim()
    $destName = if ($parts.Count -gt 1 -and $parts[1].Trim()) { $parts[1].Trim() } else { Split-Path $srcRel -Leaf }
    $src = Join-Path $Root ($srcRel -replace "/", "\")
    if (-not (Test-Path $src)) { throw "Pack source missing: $src" }
    Copy-Item $src (Join-Path $Out $destName)
}

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
