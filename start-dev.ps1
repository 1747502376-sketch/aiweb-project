# 同时启动前后端（两个新窗口）
$root = $PSScriptRoot

Write-Host "Opening backend window (port 8000)..."
Start-Process powershell -ArgumentList @(
    "-NoExit",
    "-ExecutionPolicy", "Bypass",
    "-File", (Join-Path $root "start-backend.ps1")
)

Start-Sleep -Seconds 2

Write-Host "Opening frontend window (port 5173)..."
Start-Process powershell -ArgumentList @(
    "-NoExit",
    "-ExecutionPolicy", "Bypass",
    "-File", (Join-Path $root "start-frontend.ps1")
)

Write-Host ""
Write-Host "Done."
Write-Host "  Backend:  http://127.0.0.1:8000"
Write-Host "  Frontend: http://127.0.0.1:5173  (recommended while coding)"
Write-Host "  Demo:     http://127.0.0.1:8000  (after npm run build)"
