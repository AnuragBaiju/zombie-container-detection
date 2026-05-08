# quick run-through of the demo commands for screenshots
# usage:  .\show_evidence.ps1            (pause between each)
#         .\show_evidence.ps1 -NoPause   (just dump everything)

param([switch]$NoPause)

$env:Path = "C:\Users\Aditya_Lappy\bin;C:\Users\Aditya_Lappy\AppData\Roaming\Python\Python314\Scripts;" + $env:Path

# kubectl scans PATH for *.py plugins on Windows and prints a noisy
# "File association not found" stderr line. Filter it out.
function run($cmd) {
    Write-Host ""
    Write-Host "PS> $cmd" -ForegroundColor Yellow
    & ([scriptblock]::Create($cmd)) 2>&1 | ForEach-Object {
        if ($_ -is [System.Management.Automation.ErrorRecord]) {
            if ($_.ToString() -notlike "*File association not found*") {
                Write-Host $_.ToString() -ForegroundColor DarkYellow
            }
        } else { $_ }
    }
    if (-not $NoPause) {
        Write-Host ""
        Read-Host "[enter for next]" | Out-Null
    }
}

run 'aws sts get-caller-identity'
run 'eksctl get cluster --region us-east-1'
run 'kubectl get nodes -o wide'
run 'kubectl get pods -A'
run 'kubectl get pods -n test-scenarios -o wide'
run 'kubectl get pods -n zombie-detector -o wide'
run "(kubectl logs -n zombie-detector deployment/zombie-detector --tail=400 2>`$null) | Select-String '^\[(ZOMBIE|POTENTIAL|NORMAL)\]' | Select -Last 12"
run 'kubectl logs -n zombie-detector deployment/zombie-detector --tail=80'
run 'kubectl get pods,svc -n monitoring'
