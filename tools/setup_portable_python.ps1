param(
    [string]$Version = "3.12.10"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$PyDir = Join-Path $Root "bin\python"
$SiteDir = Join-Path $PyDir "Lib\site-packages"
$WorkDir = Join-Path $Root "work"
$EmbedZip = Join-Path $WorkDir "python-$Version-embed-amd64.zip"
$WheelDir = Join-Path $WorkDir "wheels"

$MajorMinor = ($Version.Split('.')[0..1]) -join '.'
$Url = "https://www.python.org/ftp/python/$Version/python-$Version-embed-amd64.zip"

New-Item -ItemType Directory -Force -Path $WorkDir, $WheelDir | Out-Null
if (Test-Path $PyDir) { Remove-Item -Recurse -Force $PyDir }

Invoke-WebRequest -Uri $Url -OutFile $EmbedZip

Expand-Archive -Path $EmbedZip -DestinationPath $PyDir -Force

$pth = Get-ChildItem -Path $PyDir -Filter "python*._pth" | Select-Object -First 1
if (-not $pth) { throw "未找到 python*._pth 文件" }
$pthText = (Get-Content $pth.FullName -Raw) -replace '#import site', 'import site'
if ($pthText -notmatch 'Lib\\site-packages') {
    $pthText = $pthText.TrimEnd() + "`r`nLib\site-packages`r`n"
}
Set-Content -Path $pth.FullName -Value $pthText -Encoding Ascii

Remove-Item -ErrorAction SilentlyContinue "$WheelDir\*.whl"
python -m pip download --only-binary=:all: --python-version $MajorMinor --platform win_amd64 --dest $WheelDir pillow pngquant_py
if ($LASTEXITCODE -ne 0) { throw "pip download pillow, pngquant_py 失败" }

New-Item -ItemType Directory -Force -Path $SiteDir | Out-Null
foreach ($whl in Get-ChildItem -Path $WheelDir -Filter *.whl) {
    $tmpZip = "$($whl.FullName).zip"
    Copy-Item -Force $whl.FullName $tmpZip
    Expand-Archive -Path $tmpZip -DestinationPath $SiteDir -Force
    Remove-Item -Force $tmpZip
}

Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $EmbedZip, $WheelDir
$sizeMb = [math]::Round(((Get-ChildItem $PyDir -Recurse -File | Measure-Object -Property Length -Sum).Sum / 1MB), 1)
Write-Host "`nDone: bin\python\ (${sizeMb} MB)" -ForegroundColor Green
