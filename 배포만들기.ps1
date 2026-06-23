# Build a distribution zip (excludes config.json and caches)
$src = $PSScriptRoot
$stage = Join-Path $env:TEMP "pingpong_dist"
if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }
New-Item -ItemType Directory $stage | Out-Null
Copy-Item (Join-Path $src "*") $stage -Recurse -Force
Remove-Item (Join-Path $stage "config.json") -Force -ErrorAction SilentlyContinue
Get-ChildItem $stage -Recurse -Force -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force
Get-ChildItem $stage -Recurse -Filter "*.zip" | Remove-Item -Force -ErrorAction SilentlyContinue
$out = Join-Path $src "pingpong_dist.zip"
if (Test-Path $out) { Remove-Item $out -Force }
Compress-Archive -Path (Join-Path $stage "*") -DestinationPath $out
Remove-Item $stage -Recurse -Force
Write-Host ""
Write-Host ("  [DONE] " + $out)
Write-Host "  config.json excluded (receiver runs 설치.bat to create it)"
