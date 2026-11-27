param(
    [string]$IsoPath = "",
    [string]$ExePath = "",
    [string]$OutPath = "",
    [switch]$Cli
)

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
Set-Location $Root

function Run-Build(
    [string]$RootDir,
    [string]$IsoFile,
    [string]$PcFile,
    [string]$OutIso,
    [System.Collections.Concurrent.ConcurrentQueue[string]]$LogQueue
) {
    $ErrorActionPreference = "Stop"
    Set-Location $RootDir
    $env:PATH = "$RootDir\bin\python;$RootDir\bin;$env:PATH"
    try { [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false) } catch {}

    function Log([string]$Msg) {
        if ($LogQueue) { $LogQueue.Enqueue($Msg) } else { Write-Host $Msg }
    }

    function Exec([string]$Step, [scriptblock]$Cmd) {
        $prev = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        try {
            & $Cmd 2>&1 | ForEach-Object { Log "$_" }
        } finally {
            $ErrorActionPreference = $prev
        }
        if ($LASTEXITCODE -ne 0) { throw "$Step 失败 (exit code: $LASTEXITCODE)" }
    }

    function Extract-IsoFile([string]$SrcIso, [string]$TargetPath, [string]$OutFile) {
        $fs = [System.IO.File]::OpenRead($SrcIso)
        try {
            $pvd = New-Object byte[] 2048
            $fs.Position = 16 * 2048
            [void]$fs.Read($pvd, 0, 2048)
            $lba = [BitConverter]::ToUInt32($pvd, 158)
            $sz  = [BitConverter]::ToUInt32($pvd, 166)

            foreach ($part in $TargetPath.Split('/')) {
                $dir = New-Object byte[] $sz
                $fs.Position = [int64]$lba * 2048
                [void]$fs.Read($dir, 0, $sz)
                $pos = 0; $found = $false
                while ($pos -lt $sz) {
                    $rlen = $dir[$pos]
                    if ($rlen -eq 0) { $pos = ([math]::Floor($pos / 2048) + 1) * 2048; continue }
                    $nlen = $dir[$pos + 32]
                    $name = [System.Text.Encoding]::ASCII.GetString($dir, $pos + 33, $nlen).Split(';')[0]
                    if ($name -ieq $part) {
                        $lba = [BitConverter]::ToUInt32($dir, $pos + 2)
                        $sz  = [BitConverter]::ToUInt32($dir, $pos + 10)
                        $found = $true; break
                    }
                    $pos += $rlen
                }
                if (-not $found) { throw "ISO 中未找到: $TargetPath" }
            }

            $out = [System.IO.File]::Create((Join-Path $RootDir $OutFile))
            try {
                $fs.Position = [int64]$lba * 2048
                $buf = New-Object byte[] (4 * 1024 * 1024)
                $rem = [int64]$sz
                while ($rem -gt 0) {
                    $n = $fs.Read($buf, 0, [int][math]::Min($buf.Length, $rem))
                    if ($n -le 0) { break }
                    $out.Write($buf, 0, $n)
                    $rem -= $n
                }
            } finally { $out.Dispose() }
        } finally { $fs.Dispose() }
    }

    if (-not (Test-Path $IsoFile)) { throw "未找到日文原版 ISO: $IsoFile" }
    if (-not (Test-Path $PcFile))  { throw "未找到 PC 版本汉化安装程序: $PcFile" }

    New-Item -ItemType Directory -Force -Path work\DATA1\font, work\DATA1\CCC | Out-Null
    $outParent = Split-Path -Parent $OutIso
    if ($outParent) { New-Item -ItemType Directory -Force -Path $outParent | Out-Null }

    $OrigCpk = "work\DATA1_orig.cpk"
    if (-not (Test-Path $OrigCpk)) {
        Extract-IsoFile $IsoFile "PSP_GAME/USRDIR/data/DATA1.CPK" $OrigCpk
    }

    $Data1Orig = "work\DATA1_orig"
    if (-not (Test-Path "$Data1Orig\script")) {
        Exec "CriPakTools extract" { CriPakTools.exe extract -i $OrigCpk -o $Data1Orig }
    }

    if (-not (Test-Path "patch\script.csv")) { throw "缺少 patch\script.csv" }
    if (-not (Test-Path "patch\map.tsv"))    { throw "缺少 patch\map.tsv" }
    Exec "map_tool" { python -X utf8 tools\map_tool.py fill -c patch\script.csv -m patch\map.tsv -e $PcFile -o work\script.csv }
    $ScriptCsv = "work\script.csv"

    Exec "gen_glyph_table"  { python -X utf8 tools\gen_glyph_table.py -i "$ScriptCsv,patch\eboot.csv" -f "$Data1Orig\font\font_16_a.txt" -o work\glyph_table.csv }
    Exec "patch_tool"       { python -X utf8 tools\patch_tool.py -b patch\EBOOT.BIN -c patch\eboot.csv -e cp932 -g work\glyph_table.csv -o work\EBOOT.BIN }
    Exec "patch_jis2ucs"    { python -X utf8 tools\patch_jis2ucs.py -i "$Data1Orig\CCC\jis2ucs.bin" -o work\DATA1\CCC\jis2ucs.bin -g work\glyph_table.csv }
    Exec "gen_font_index"   { python -X utf8 tools\gen_font_index.py -i work\glyph_table.csv -o work\DATA1\font\font_16_a.txt }
    Exec "script_tool write" { python -X utf8 tools\script_tool.py write -i "$Data1Orig\script" -o work\DATA1\script -c $ScriptCsv -g work\glyph_table.csv }

    Exec "gen_glyph_images" { python -X utf8 tools\gen_glyph_images.py -i work\glyph_table.csv -f patch\NotoSansCJKsc-Medium.otf --font-size 16 --offset-y -1 -o work\font_16_a0.png }
    Exec "pngquant"         { pngquant.exe --ext .png --force 16 work\font_16_a0.png }
    Exec "ext_tool"         { python -X utf8 tools\ext_tool.py encode -i work\font_16_a0.png -o work\DATA1\font\font_16_a0.ext }
    Copy-Item -Force work\DATA1\font\font_16_a0.ext work\DATA1\font\font_16_a1.ext
    Copy-Item -Force work\DATA1\font\font_16_a0.ext work\DATA1\font\font_16_a2.ext

    if (Test-Path "patch\visual") {
        Exec "pac_tool" { python -X utf8 tools\pac_tool.py encode -i patch\visual -o work\DATA1\visual --orig "$Data1Orig\visual" }
    }

    Exec "CriPakTools replace" { CriPakTools.exe replace -i $OrigCpk -d work\DATA1 -o work\DATA1.CPK }

    Copy-Item -Force $IsoFile $OutIso
    Exec "UMDReplaceK" { UMDReplaceK.exe $OutIso "PSP_GAME/USRDIR/data/DATA1.CPK" "work\DATA1.CPK" "PSP_GAME/SYSDIR/EBOOT.BIN" "work\EBOOT.BIN" }
    Log "`n打包完成: $OutIso"
}

if ($Cli) {
    if (-not $OutPath -and $IsoPath) {
        $OutPath = Join-Path $Root "$([System.IO.Path]::GetFileNameWithoutExtension($IsoPath))_chs.iso"
    }
    Run-Build $Root $IsoPath $ExePath $OutPath $null
    exit 0
}

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()

$form = New-Object System.Windows.Forms.Form
$form.Text = "PSP 1/2 summer+ 汉化打包工具"
$form.Size = New-Object System.Drawing.Size(640, 520)
$form.StartPosition = "CenterScreen"
$form.FormBorderStyle = "FixedDialog"
$form.MaximizeBox = $false
$form.Font = New-Object System.Drawing.Font("Microsoft YaHei UI", 9)

function Add-FileRow([int]$y, [string]$labelText, [string]$defaultVal, [scriptblock]$onBrowse) {
    $lbl = New-Object System.Windows.Forms.Label
    $lbl.Text = $labelText
    $lbl.Location = [System.Drawing.Point]::new(16, $y)
    $lbl.Size = New-Object System.Drawing.Size(597, 20)
    $form.Controls.Add($lbl)

    $tb = New-Object System.Windows.Forms.TextBox
    $tb.Text = $defaultVal
    $tb.Location = [System.Drawing.Point]::new(16, $y + 22)
    $tb.Size = New-Object System.Drawing.Size(510, 24)
    $tb.AllowDrop = $true
    $tb.Add_DragEnter({ param($s, $e) if ($e.Data.GetDataPresent([Windows.Forms.DataFormats]::FileDrop)) { $e.Effect = "Copy" } })
    $tb.Add_DragDrop({ param($s, $e) $files = $e.Data.GetData([Windows.Forms.DataFormats]::FileDrop); if ($files.Count -gt 0) { $s.Text = $files[0] } })
    $form.Controls.Add($tb)

    $btn = New-Object System.Windows.Forms.Button
    $btn.Text = "浏览..."
    $btn.Location = [System.Drawing.Point]::new(535, $y + 21)
    $btn.Size = New-Object System.Drawing.Size(78, 26)
    $btn.Add_Click($onBrowse)
    $form.Controls.Add($btn)
    return $tb
}

$defIso  = $IsoPath
$defPc   = $ExePath
$defOut  = $OutPath
$script:outEdited = [bool]$OutPath
$script:autoUpdatingOut = $false

$tbIso = Add-FileRow 14 "PSP 日文原版 ISO：" $defIso {
    $dlg = New-Object System.Windows.Forms.OpenFileDialog
    $dlg.Filter = "PSP ISO (*.iso)|*.iso|所有文件 (*.*)|*.*"
    if ($dlg.ShowDialog() -eq "OK") { $tbIso.Text = $dlg.FileName }
}
$tbPc = Add-FileRow 70 "PC 版本汉化安装程序：" $defPc {
    $dlg = New-Object System.Windows.Forms.OpenFileDialog
    $dlg.Filter = "PC 版本汉化安装程序 (*.exe)|*.exe|所有文件 (*.*)|*.*"
    if ($dlg.ShowDialog() -eq "OK") { $tbPc.Text = $dlg.FileName }
}
$tbOut = Add-FileRow 126 "输出汉化 ISO：" $defOut {
    $dlg = New-Object System.Windows.Forms.SaveFileDialog
    $dlg.Filter = "PSP ISO (*.iso)|*.iso"
    if ($tbOut.Text.Trim()) { $dlg.FileName = [System.IO.Path]::GetFileName($tbOut.Text) }
    if ($dlg.ShowDialog() -eq "OK") { $tbOut.Text = $dlg.FileName }
}

$tbOut.Add_TextChanged({
    if (-not $script:autoUpdatingOut) {
        $script:outEdited = [bool]$tbOut.Text.Trim()
    }
})

$tbIso.Add_TextChanged({
    if (-not $script:outEdited -and $tbIso.Text.Trim()) {
        $dir = [System.IO.Path]::GetDirectoryName($tbIso.Text.Trim())
        $name = [System.IO.Path]::GetFileNameWithoutExtension($tbIso.Text.Trim())
        if ($dir -and $name) {
            $script:autoUpdatingOut = $true
            try { $tbOut.Text = Join-Path $dir "${name}_chs.iso" } finally { $script:autoUpdatingOut = $false }
        }
    }
})

$btnRun = New-Object System.Windows.Forms.Button
$btnRun.Text = "开始打包"
$btnRun.Location = New-Object System.Drawing.Point(16, 188)
$btnRun.Size = New-Object System.Drawing.Size(597, 34)
$form.Controls.Add($btnRun)

$tbLog = New-Object System.Windows.Forms.TextBox
$tbLog.Location = New-Object System.Drawing.Point(16, 232)
$tbLog.Size = New-Object System.Drawing.Size(597, 236)
$tbLog.Multiline = $true
$tbLog.ScrollBars = "Vertical"
$tbLog.ReadOnly = $true
$tbLog.Font = New-Object System.Drawing.Font("Consolas", 9)
$form.Controls.Add($tbLog)

$script:queue = $null
$script:ps = $null
$script:async = $null

$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 100
$timer.Add_Tick({
    $line = $null
    while ($script:queue -and $script:queue.TryDequeue([ref]$line)) {
        $tbLog.AppendText("$line`r`n")
    }
    if ($script:async -and $script:async.IsCompleted) {
        $timer.Stop()
        $errMsg = $null
        try {
            [void]$script:ps.EndInvoke($script:async)
            if ($script:ps.HadErrors -or $script:ps.Streams.Error.Count -gt 0) {
                $errMsg = ($script:ps.Streams.Error | Select-Object -First 1).ToString()
                if (-not $errMsg) { $errMsg = "打包过程中发生未知错误" }
            }
        } catch {
            $errMsg = if ($_.Exception.InnerException) { $_.Exception.InnerException.Message } else { $_.Exception.Message }
        } finally {
            $script:ps.Dispose()
            $script:ps = $null
            $script:async = $null
            $btnRun.Enabled = $true
            $btnRun.Text = "开始打包"
        }
        if ($errMsg) {
            $tbLog.AppendText("[ERROR] $errMsg`r`n")
            [System.Windows.Forms.MessageBox]::Show($errMsg, "打包失败", "OK", "Error") | Out-Null
        } else {
            [System.Windows.Forms.MessageBox]::Show("汉化 ISO 已生成：`n$($tbOut.Text)", "打包完成", "OK", "Information") | Out-Null
        }
    }
})

$btnRun.Add_Click({
    $isoVal = $tbIso.Text.Trim()
    $pcVal  = $tbPc.Text.Trim()
    $outVal = $tbOut.Text.Trim()
    if (-not $isoVal -or -not (Test-Path -LiteralPath $isoVal -PathType Leaf)) { [System.Windows.Forms.MessageBox]::Show("请选择有效的 PSP 日文原版 ISO 文件。", "提示", "OK", "Warning") | Out-Null; return }
    if (-not $pcVal  -or -not (Test-Path -LiteralPath $pcVal  -PathType Leaf)) { [System.Windows.Forms.MessageBox]::Show("请选择有效的 PC 版本汉化安装程序（.exe）。", "提示", "OK", "Warning") | Out-Null; return }
    if (-not $outVal) { [System.Windows.Forms.MessageBox]::Show("请指定输出 ISO 路径。", "提示", "OK", "Warning") | Out-Null; return }

    $tbLog.Clear()
    $btnRun.Enabled = $false
    $btnRun.Text = "打包中，请稍候..."

    $script:queue = New-Object 'System.Collections.Concurrent.ConcurrentQueue[string]'
    $script:ps = [PowerShell]::Create()
    $fnDef = (Get-Command Run-Build).Definition
    [void]$script:ps.AddScript("function Run-Build { $fnDef }")
    [void]$script:ps.AddStatement().AddCommand("Run-Build").AddArgument($Root).AddArgument($tbIso.Text).AddArgument($tbPc.Text).AddArgument($tbOut.Text).AddArgument($script:queue)
    $script:async = $script:ps.BeginInvoke()
    $timer.Start()
})

$form.Add_FormClosing({
    $timer.Stop()
    if ($script:ps) { try { $script:ps.Stop(); $script:ps.Dispose() } catch {} }
})

[void]$form.ShowDialog()
