param(
    [string]$SshHost = "43.154.239.41",
    [string]$SshUser = "ubuntu",
    [string]$IdentityFile = (Join-Path $env:USERPROFILE ".ssh\alphalab_conexus_ed25519"),
    [int]$RemotePort = 18000,
    [int]$LocalPort = 8000
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $IdentityFile)) {
    throw "AlphaLab Conexus tunnel key was not found: $IdentityFile"
}

$ssh = (Get-Command ssh -ErrorAction Stop).Source
$remote = "${SshUser}@${SshHost}"
$forward = "127.0.0.1:${RemotePort}:127.0.0.1:${LocalPort}"

Write-Host "Connecting AlphaLab API to cloud Conexus through an SSH tunnel..." -ForegroundColor Cyan
& $ssh `
    -N `
    -T `
    -i $IdentityFile `
    -o BatchMode=yes `
    -o ExitOnForwardFailure=yes `
    -o ServerAliveInterval=30 `
    -o ServerAliveCountMax=3 `
    -R $forward `
    $remote

if ($LASTEXITCODE -ne 0) {
    throw "AlphaLab Conexus SSH tunnel exited with code $LASTEXITCODE."
}
