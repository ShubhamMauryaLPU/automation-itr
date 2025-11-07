$serviceName = "celery-worker"
$status = (Get-Service -Name $serviceName).Status

if ($status -ne "Running") {
    Write-Output "$(Get-Date) - $serviceName not running, restarting..."
    nssm restart $serviceName
} else {
    Write-Output "$(Get-Date) - $serviceName is healthy."
}
