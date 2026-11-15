# WFP 离线安装（Windows，DR-73）：全部依赖来自随包 wheelhouse，不访问外网。
# 用法： .\scripts\install-offline.ps1 -Wheelhouse .\wheelhouse -Python python
param(
  [string]$Wheelhouse = ".\wheelhouse",
  [string]$Python = "python"
)

$ErrorActionPreference = "Stop"
if (-not (Test-Path $Wheelhouse)) {
  throw "缺少 wheelhouse 目录：$Wheelhouse（离线分发包应随附所有 wheel）"
}

& $Python -m pip install --no-index --find-links $Wheelhouse --upgrade wfp
if ($LASTEXITCODE -ne 0) { throw "离线安装失败" }

& $Python -m wfp.cli doctor
if ($LASTEXITCODE -ne 0) { throw "环境自诊断未通过，请按提示修复" }

Write-Host "WFP 安装完成。启动：wfp serve --open" -ForegroundColor Green
