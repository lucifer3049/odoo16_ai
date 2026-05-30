<#
.SYNOPSIS
    一鍵升級 Odoo 模組並重啟 web 服務。

.DESCRIPTION
    省去每次手動打 docker compose 指令。流程：
      1.（選用）-Build 時重建 image（Dockerfile 有改才需要，例如新增 OCR/pgvector 依賴）
      2. 確保容器啟動
      3. 在 web 容器內執行 odoo -u <模組> -d <DB> --stop-after-init（跑升級與 migration）
      4. 重啟 web 服務讓新狀態生效

.PARAMETER Db
    資料庫名稱。未指定時會自動偵測（若只有一個使用者資料庫）。

.PARAMETER Modules
    要升級的模組，逗號分隔。預設 odoo_ai_assistant。

.PARAMETER Build
    加上此參數會先重建 image（Dockerfile 變更時使用）。

.EXAMPLE
    .\upgrade.ps1
    .\upgrade.ps1 -Db mydb
    .\upgrade.ps1 -Build           # Dockerfile 改過（OCR、pgvector 等）時用
#>

param(
    [string]$Db = "",
    [string]$Modules = "odoo_ai_assistant",
    [switch]$Build
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

function Step($msg) { Write-Host "`n=== $msg ===" -ForegroundColor Cyan }

# 1. 選用：重建 image
if ($Build) {
    Step "重建 image（docker compose build）"
    docker compose build
    if ($LASTEXITCODE -ne 0) { throw "image 重建失敗" }
}

# 2. 確保容器啟動
Step "啟動容器（docker compose up -d）"
docker compose up -d
if ($LASTEXITCODE -ne 0) { throw "容器啟動失敗" }

# 3. 自動偵測資料庫名稱
if ([string]::IsNullOrWhiteSpace($Db)) {
    Step "自動偵測資料庫名稱"
    $detected = docker compose exec -T db psql -U odoo -d postgres -tAc `
        "SELECT datname FROM pg_database WHERE datname NOT IN ('postgres','template0','template1')"
    $dbs = @($detected -split "`n" | ForEach-Object { $_.Trim() } | Where-Object { $_ -ne "" })

    if ($dbs.Count -eq 1) {
        $Db = $dbs[0]
        Write-Host "偵測到資料庫：$Db" -ForegroundColor Green
    }
    elseif ($dbs.Count -eq 0) {
        throw "找不到任何資料庫，請先在瀏覽器建立後再執行，或用 -Db 指定。"
    }
    else {
        throw "偵測到多個資料庫（$($dbs -join ', ')），請用 -Db 指定要升級哪一個。"
    }
}

# 4. 執行升級（含 migration）
Step "升級模組 $Modules（資料庫 $Db）"
docker compose exec -T web odoo -u $Modules -d $Db --stop-after-init
if ($LASTEXITCODE -ne 0) { throw "模組升級失敗，請查看上方 log。" }

# 5. 重啟 web
Step "重啟 web 服務"
docker compose restart web
if ($LASTEXITCODE -ne 0) { throw "web 重啟失敗" }

Write-Host "`n✅ 升級完成！可重新整理瀏覽器（http://localhost:8069）。" -ForegroundColor Green
