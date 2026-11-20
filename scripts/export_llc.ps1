[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RootDir = Split-Path -Parent $ScriptDir
$VenvPython = Join-Path $RootDir ".venv\Scripts\python.exe"
$ExportScript = Join-Path $RootDir "scripts\export_annotations_to_llc.py"

Write-Host "========================================================" -ForegroundColor Cyan
Write-Host "       LosslessCut (.llc) 標註專案檔一鍵導出工具        " -ForegroundColor Cyan
Write-Host "========================================================" -ForegroundColor Cyan
Write-Host ""

$Streamer = ""
if ($args.Count -gt 0) {
    $Streamer = $args[0]
} else {
    Write-Host "請輸入要導出 .llc 專案檔的主播名稱：" -ForegroundColor Yellow
    Write-Host "  (例如：-菫時-、秋葵，若直接按 Enter 則導出所有主播)"
    $Streamer = Read-Host "主播名稱"
}

$Streamer = $Streamer.Trim(" `"'")

if ([string]::IsNullOrWhiteSpace($Streamer)) {
    Write-Host ""
    Write-Host "[*] 正在為所有主播導出 LosslessCut (.llc) 專案檔..." -ForegroundColor Green
    & "$VenvPython" "$ExportScript"
} else {
    Write-Host ""
    Write-Host "[*] 正在為【$Streamer】導出 LosslessCut (.llc) 專案檔..." -ForegroundColor Green
    & "$VenvPython" "$ExportScript" "--streamer=$Streamer"
}

Write-Host ""
Write-Host "請按 Enter 鍵結束..."
[void][System.Console]::ReadLine()
