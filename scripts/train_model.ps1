[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RootDir = Split-Path -Parent $ScriptDir
$VenvPython = Join-Path $RootDir ".venv\Scripts\python.exe"
$TrainScript = Join-Path $ScriptDir "train_streamer.py"
$AnnDir = Join-Path $RootDir "data\annotations"

Write-Host "========================================================" -ForegroundColor Cyan
Write-Host "       AutoSlicer-v2 主播專屬歌唱模型訓練工具             " -ForegroundColor Cyan
Write-Host "========================================================" -ForegroundColor Cyan
Write-Host ""

$Streamer = ""

if ($args.Count -gt 0) {
    $Streamer = $args[0]
} else {
    # Scan available streamers with annotations
    $Streamers = @()
    if (Test-Path $AnnDir) {
        $Dirs = Get-ChildItem -Path $AnnDir -Directory
        foreach ($d in $Dirs) {
            $jsonCount = (Get-ChildItem -Path $d.FullName -Filter "*.segments.json").Count
            if ($jsonCount -gt 0) {
                $Streamers += [PSCustomObject]@{
                    Name = $d.Name
                    Sessions = $jsonCount
                }
            }
        }
    }

    if ($Streamers.Count -eq 0) {
        Write-Host "錯誤：在 data\annotations 中未找到任何已標註的主播資料！" -ForegroundColor Red
        Write-Host "請先使用 import_llc.bat 匯入 .llc 專案檔。" -ForegroundColor Yellow
        exit 1
    }

    Write-Host "目前已就緒的主播資料：" -ForegroundColor Yellow
    for ($i = 0; $i -lt $Streamers.Count; $i++) {
        $st = $Streamers[$i]
        Write-Host ("  [{0}] {1} ({2} 場歌回)" -f ($i + 1), $st.Name, $st.Sessions) -ForegroundColor Green
    }
    Write-Host ""
    $Choice = Read-Host "請輸入要訓練的主播編號或名稱 (預設: 1)"
    if ([string]::IsNullOrWhiteSpace($Choice)) {
        $Choice = "1"
    }

    $num = 0
    if ([int]::TryParse($Choice, [ref]$num) -and $num -ge 1 -and $num -le $Streamers.Count) {
        $Streamer = $Streamers[$num - 1].Name
    } else {
        $Streamer = $Choice.Trim()
    }
}

Write-Host ""
Write-Host "[*] 選定訓練主播: $Streamer" -ForegroundColor Cyan
Write-Host "[*] 正在啟動訓練流水線..." -ForegroundColor Cyan
Write-Host ""

& "$VenvPython" "$TrainScript" "$Streamer"

$ExitCode = $LASTEXITCODE

Write-Host ""
if ($ExitCode -eq 0) {
    Write-Host "========================================================" -ForegroundColor Green
    Write-Host "模型訓練圓滿完成！" -ForegroundColor Green
    Write-Host "模型權重位於: runs\$Streamer\best.pt" -ForegroundColor Green
    Write-Host "========================================================" -ForegroundColor Green
} else {
    Write-Host "========================================================" -ForegroundColor Red
    Write-Host "訓練過程遭遇異常 (Exit Code: $ExitCode)" -ForegroundColor Red
    Write-Host "========================================================" -ForegroundColor Red
}

if ([Environment]::UserInteractive -and -not [Console]::IsInputRedirected) {
    Write-Host ""
    Write-Host "請按 Enter 鍵結束..."
    [void][System.Console]::ReadLine()
}

exit $ExitCode
