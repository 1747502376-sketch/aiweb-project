# 启动前端开发服务器（Vue + Vite）
Set-Location (Join-Path $PSScriptRoot "frontend")
if (-not (Test-Path "node_modules")) {
    Write-Host "Installing frontend dependencies..."
    npm install --registry=https://registry.npmmirror.com
}
Write-Host "Starting frontend at http://127.0.0.1:5173 ..."
npm run dev
