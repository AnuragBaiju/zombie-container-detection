# launcher: aws auth -> prometheus port-forward -> streamlit
# usage:  .\run_dashboard.ps1
# stop:   ctrl-c (kills both this script and the port-forward)

$ErrorActionPreference = "Stop"

$env:Path = "C:\Users\Aditya_Lappy\bin;" +
            "C:\Users\Aditya_Lappy\AppData\Roaming\Python\Python314\Scripts;" +
            "C:\Users\Aditya_Lappy\AppData\Local\Python\bin;" +
            $env:Path

Write-Host "1/4  verify aws identity" -ForegroundColor Cyan
$id = aws sts get-caller-identity 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "    aws auth failed. dashboard will start in OFFLINE mode." -ForegroundColor Yellow
    Write-Host "    fix: aws configure   (or set AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY)" -ForegroundColor Yellow
} else {
    Write-Host "    ok: $id" -ForegroundColor Green
}

Write-Host "2/4  refresh kubeconfig" -ForegroundColor Cyan
aws eks update-kubeconfig --name zombie-detector-cluster --region us-east-1 2>&1 | Out-Null

Write-Host "3/4  start prometheus port-forward (background)" -ForegroundColor Cyan
$pf = Start-Process -PassThru -WindowStyle Hidden -FilePath "kubectl" `
      -ArgumentList "port-forward","-n","monitoring","svc/prometheus-server","9090:9090"
Start-Sleep -Seconds 3

$probe = try { Invoke-WebRequest "http://localhost:9090/-/ready" -TimeoutSec 3 -UseBasicParsing } catch { $null }
if ($probe -and $probe.StatusCode -eq 200) {
    Write-Host "    prometheus reachable on http://localhost:9090" -ForegroundColor Green
} else {
    Write-Host "    prometheus not reachable. dashboard will fall back to OFFLINE mode." -ForegroundColor Yellow
}

Write-Host "4/4  launch streamlit at http://localhost:8501" -ForegroundColor Cyan
$env:PROMETHEUS_URL = "http://localhost:9090"
$env:REFRESH_SECONDS = "30"

try {
    streamlit run dashboard/app.py
} finally {
    if ($pf -and -not $pf.HasExited) { Stop-Process -Id $pf.Id -Force }
}
