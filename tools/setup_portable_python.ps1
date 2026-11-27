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

Write-Host "[1/4] 下载 Python $Version Embeddable (64-bit)..."
Invoke-WebRequest -Uri $Url -OutFile $EmbedZip

Write-Host "[2/4] 解压到 bin\python..."
Expand-Archive -Path $EmbedZip -DestinationPath $PyDir -Force

# 启用 site-packages
$pth = Get-ChildItem -Path $PyDir -Filter "python*._pth" | Select-Object -First 1
if (-not $pth) { throw "未找到 python*._pth 文件" }
$pthText = (Get-Content $pth.FullName -Raw) -replace '#import site', 'import site'
if ($pthText -notmatch 'Lib\\site-packages') {
    $pthText = $pthText.TrimEnd() + "`r`nLib\site-packages`r`n"
}
Set-Content -Path $pth.FullName -Value $pthText -Encoding Ascii

Write-Host "[3/4] 下载并解压 Pillow (win_amd64, Python $MajorMinor)..."
Remove-Item -ErrorAction SilentlyContinue "$WheelDir\*.whl"
python -m pip download --only-binary=:all: --python-version $MajorMinor --platform win_amd64 --dest $WheelDir pillow
if ($LASTEXITCODE -ne 0) { throw "pip download pillow 失败" }

New-Item -ItemType Directory -Force -Path $SiteDir | Out-Null
foreach ($whl in Get-ChildItem -Path $WheelDir -Filter *.whl) {
    $tmpZip = "$($whl.FullName).zip"
    Copy-Item -Force $whl.FullName $tmpZip
    Expand-Archive -Path $tmpZip -DestinationPath $SiteDir -Force
    Remove-Item -Force $tmpZip
}

Write-Host "[4/4] 验证便携版 Python 与 Pillow..."
& "$PyDir\python.exe" -X utf8 -c "from PIL import Image, ImageDraw, ImageFont; import sys; print('Python:', sys.version.split()[0], '| PIL:', Image.__file__)"
if ($LASTEXITCODE -ne 0) { throw "便携版 Python 验证失败" }

Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $EmbedZip, $WheelDir
$sizeMb = [math]::Round(((Get-ChildItem $PyDir -Recurse -File | Measure-Object -Property Length -Sum).Sum / 1MB), 1)
Write-Host "`n[DONE] 便携版 Python 已就绪: bin\python\ (${sizeMb} MB)" -ForegroundColor Green
