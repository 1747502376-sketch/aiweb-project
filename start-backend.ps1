# 启动后端（FastAPI）
Set-Location $PSScriptRoot
Write-Host "Starting backend at http://127.0.0.1:8000 ..."
uvicorn main:app --host 127.0.0.1 --port 8000 --reload
