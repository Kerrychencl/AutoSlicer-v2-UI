[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RootDir = Split-Path -Parent $ScriptDir
$VenvPython = Join-Path $RootDir ".venv\Scripts\python.exe"
$ConverterScript = Join-Path $ScriptDir "convert_llc_to_annotations.py"

Write-Host "========================================================" -ForegroundColor Cyan
Write-Host "       LosslessCut (.llc) 剪輯時間軸自動匯入工具         " -ForegroundColor Cyan
Write-Host "========================================================" -ForegroundColor Cyan
Write-Host ""

$Targets = @()

if ($args.Count -gt 0) {
    $Targets = $args
} else {
    Write-Host "使用說明：" -ForegroundColor Yellow
    Write-Host "  1. 直接貼上 .llc 專案檔案路徑（如: xxx-proj.llc）"
    Write-Host "  2. 或貼上影片檔案（若同目錄已有 .llc 專案，將自動關聯）"
    Write-Host "  3. 或貼上放有錄播的整個資料夾路徑（會自動掃描所有 .llc）"
    Write-Host "  4. 也可直接將多個檔案或資料夾直接拖曳到 import_llc.bat 上！"
    Write-Host ""
    $InputPath = Read-Host "請輸入 .llc 檔案路徑、影片路徑 或 資料夾路徑"
    if (-not [string]::IsNullOrWhiteSpace($InputPath)) {
        $Matches = [regex]::Matches($InputPath, '"([^"]+)"|(\S+)')
        foreach ($m in $Matches) {
            $val = if ($m.Groups[1].Success) { $m.Groups[1].Value } else { $m.Groups[2].Value }
            if (-not [string]::IsNullOrWhiteSpace($val)) {
                $Targets += $val.Trim()
            }
        }
    }
}

if ($Targets.Count -eq 0) {
    Write-Host "未輸入任何目標，程式結束。" -ForegroundColor Yellow
    exit 0
}

Write-Host "[*] 開始讀取並轉換打軸資料..." -ForegroundColor Green
Write-Host ""

& "$VenvPython" "$ConverterScript" @Targets

$ExitCode = $LASTEXITCODE

Write-Host ""
if ($ExitCode -eq 0) {
    Write-Host "========================================================" -ForegroundColor Green
    Write-Host "打軸表生成完畢！" -ForegroundColor Green
    Write-Host "成果已儲存至各主播專屬目錄:" -ForegroundColor Green
    Write-Host "  - Excel 打軸表: data\annotations\<主播名稱>\" -ForegroundColor Green
    Write-Host "  - 訓練清單:     data\raw\<主播名稱> 标注\" -ForegroundColor Green
    Write-Host "========================================================" -ForegroundColor Green
} else {
    Write-Host "========================================================" -ForegroundColor Yellow
    Write-Host "轉換未完成或部分項目需要確認 (Exit Code: $ExitCode)" -ForegroundColor Yellow
    Write-Host "========================================================" -ForegroundColor Yellow
}

if ([Environment]::UserInteractive -and -not [Console]::IsInputRedirected) {
    Write-Host ""
    Write-Host "請按 Enter 鍵結束..."
    [void][System.Console]::ReadLine()
}

exit $ExitCode
