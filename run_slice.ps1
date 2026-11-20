[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvPython = Join-Path $ScriptDir ".venv\Scripts\python.exe"
$OutDir = Join-Path $ScriptDir "out"
$InferScript = Join-Path $ScriptDir "src\infer.py"

Write-Host "========================================================" -ForegroundColor Cyan
Write-Host "       AutoSlicer-v2 直播錄播歌回自動切片工具            " -ForegroundColor Cyan
Write-Host "========================================================" -ForegroundColor Cyan
Write-Host ""

# 1. 偵測可用模型 (自訓模型優先)
$AvailableModels = @()
$RunsDir = Join-Path $ScriptDir "runs"
if (Test-Path $RunsDir) {
    Get-ChildItem -Path $RunsDir -Directory | ForEach-Object {
        $pt = Join-Path $_.FullName "best.pt"
        if (Test-Path $pt) {
            $AvailableModels += [PSCustomObject]@{
                Name = $_.Name
                Path = $pt
                Type = "自訓模型"
            }
        }
    }
}
$DefaultPt = Join-Path $ScriptDir "best.pt"
if (Test-Path $DefaultPt) {
    $AvailableModels += [PSCustomObject]@{
        Name = "阿梓 Azusa"
        Path = $DefaultPt
        Type = "官方預設"
    }
}

if ($AvailableModels.Count -eq 0) {
    Write-Host "錯誤：找不到任何可用的模型權重檔案 (*.pt)" -ForegroundColor Red
    exit 1
}

# 2. 選擇模型
$SelectedModel = $AvailableModels[0]
if ($AvailableModels.Count -gt 1) {
    if (-not [Environment]::UserInteractive -or [Console]::IsInputRedirected) {
        $SelectedModel = $AvailableModels[0]
    } else {
        Write-Host "請選擇要使用的歌姬模型：" -ForegroundColor Yellow
        for ($i = 0; $i -lt $AvailableModels.Count; $i++) {
            $m = $AvailableModels[$i]
            $tag = if ($i -eq 0) { " [預設]" } else { "" }
            Write-Host "  [$($i+1)] $($m.Name) ($($m.Type))$tag"
        }
        $choice = Read-Host "請輸入編號 (直接按 Enter 選擇預設 1)"
        if (-not [string]::IsNullOrWhiteSpace($choice)) {
            $idx = 0
            if ([int]::TryParse($choice, [ref]$idx)) {
                $idx = $idx - 1
                if ($idx -ge 0 -and $idx -lt $AvailableModels.Count) {
                    $SelectedModel = $AvailableModels[$idx]
                }
            }
        }
    }
}
$Checkpoint = $SelectedModel.Path
Write-Host "[*] 已選用模型 : $($SelectedModel.Name)" -ForegroundColor Green
Write-Host ""

# 3. 收集輸入目標 (支援多檔案批次拖曳)
$TargetInputs = @()
if ($args.Count -gt 0) {
    foreach ($a in $args) {
        $t = $a.Trim(" `"'")
        if (-not [string]::IsNullOrWhiteSpace($t)) {
            $TargetInputs += $t
        }
    }
} else {
    Write-Host "使用說明：" -ForegroundColor Yellow
    Write-Host "  1. 直接貼上 B 站影片網址、BV 號或短連結 (例如 BVxxxxxxxxxx)"
    Write-Host "  2. 或輸入本地錄播檔案路徑 (.mp4, .flv, .mkv 等)"
    Write-Host "  3. 可直接一次選取多個影片檔案拖曳到 run_slice.bat 上批次切片！"
    Write-Host ""
    $rawIn = Read-Host "請輸入 B 站網址 / BV 號 或 本地影片路徑"
    if (-not [string]::IsNullOrWhiteSpace($rawIn)) {
        $matches = [regex]::Matches($rawIn, '"([^"]+)"|(\S+)')
        foreach ($m in $matches) {
            $p = if ($m.Groups[1].Success) { $m.Groups[1].Value } else { $m.Groups[2].Value }
            $p = $p.Trim(" `"'")
            if (-not [string]::IsNullOrWhiteSpace($p)) {
                $TargetInputs += $p
            }
        }
    }
}

if ($TargetInputs.Count -eq 0) {
    Write-Host "未輸入任何目標，程式結束。" -ForegroundColor Yellow
    exit 0
}

# 4. 輸出目錄設定 (對整批檔案統一設定)
$DataOutDir = Join-Path $ScriptDir "out"
if (-not (Test-Path $DataOutDir)) {
    New-Item -ItemType Directory -Path $DataOutDir | Out-Null
}

$ClipOutMode = 1
if (-not [Environment]::UserInteractive -or [Console]::IsInputRedirected) {
    $ClipOutMode = 1
} else {
    Write-Host ""
    Write-Host "偵測到共計 $($TargetInputs.Count) 個目標，請選擇切片與 .llc 存放目錄：" -ForegroundColor Yellow
    Write-Host "  [1] 與各原影片相同目錄 [預設]"
    Write-Host "  [2] 各原影片目錄下的獨立子資料夾 (【切片】)"
    Write-Host "  [3] 工具預設輸出目錄 ($DataOutDir)"
    $outChoice = Read-Host "請輸入編號 (直接按 Enter 選擇預設 1)"
    if ($outChoice -eq "2") {
        $ClipOutMode = 2
    } elseif ($outChoice -eq "3") {
        $ClipOutMode = 3
    } else {
        $ClipOutMode = 1
    }
}

$SuccessCount = 0
$FailCount = 0

for ($idx = 0; $idx -lt $TargetInputs.Count; $idx++) {
    $TargetInput = $TargetInputs[$idx]
    Write-Host ""
    Write-Host "--------------------------------------------------------" -ForegroundColor DarkCyan
    Write-Host ("[{0}/{1}] 正在處理: {2}" -f ($idx + 1), $TargetInputs.Count, $TargetInput) -ForegroundColor Cyan
    Write-Host "--------------------------------------------------------" -ForegroundColor DarkCyan

    $ClipOutDir = $DataOutDir
    if (Test-Path $TargetInput -PathType Leaf) {
        $VideoDir = (Get-Item $TargetInput).DirectoryName
        if ($ClipOutMode -eq 2) {
            $ClipOutDir = Join-Path $VideoDir "【切片】"
        } elseif ($ClipOutMode -eq 3) {
            $ClipOutDir = $DataOutDir
        } else {
            $ClipOutDir = $VideoDir
        }
    }

    if (-not (Test-Path $ClipOutDir)) {
        New-Item -ItemType Directory -Path $ClipOutDir | Out-Null
    }

    & "$VenvPython" "$InferScript" --input "$TargetInput" --checkpoint "$Checkpoint" --out-dir "$DataOutDir" --clip-out-dir "$ClipOutDir" --cut-video

    if ($LASTEXITCODE -eq 0) {
        Write-Host "  [✓] 切片完成！影片與 .llc 已存至: $ClipOutDir" -ForegroundColor Green
        $SuccessCount++
    } else {
        Write-Host "  [✗] 處理失敗 (Exit Code: $LASTEXITCODE)" -ForegroundColor Red
        $FailCount++
    }
}

Write-Host ""
Write-Host "========================================================" -ForegroundColor Green
Write-Host ("批次切片處理完成！成功: {0} 場，失敗: {1} 場" -f $SuccessCount, $FailCount) -ForegroundColor Green
Write-Host "========================================================" -ForegroundColor Green

if ([Environment]::UserInteractive -and -not [Console]::IsInputRedirected) {
    Write-Host "請按 Enter 鍵結束..."
    [void][System.Console]::ReadLine()
}
exit $FailCount
