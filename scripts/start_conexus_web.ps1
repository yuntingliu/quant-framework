param(
    [string]$ConexusRoot = (Join-Path (Split-Path $PSScriptRoot -Parent) '..\Conexus'),
    [string]$ProjectRoot = (Split-Path $PSScriptRoot -Parent),
    [int]$Port = 3000
)

$ErrorActionPreference = 'Stop'
$ConexusRoot = (Resolve-Path $ConexusRoot).Path
$ProjectRoot = (Resolve-Path $ProjectRoot).Path

function Import-DotEnv([string]$Path) {
    if (-not (Test-Path $Path)) { return }
    foreach ($line in Get-Content $Path) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith('#') -or -not $trimmed.Contains('=')) { continue }
        $parts = $trimmed.Split('=', 2)
        $name = $parts[0].Trim()
        $value = $parts[1].Trim().Trim('"').Trim("'")
        if ($name -and -not [Environment]::GetEnvironmentVariable($name, 'Process')) {
            [Environment]::SetEnvironmentVariable($name, $value, 'Process')
        }
    }
}

Import-DotEnv (Join-Path $ConexusRoot 'backend\.env')

$env:NODE_ENV = 'development'
$env:CONEXUS_PROJECT_ROOT = $ProjectRoot
$env:CONEXUS_WEB_DIST = (Join-Path $ConexusRoot 'dist\web')
$env:CONEXUS_WEB_HOST = '127.0.0.1'
$env:CONEXUS_WEB_PORT = [string]$Port
$env:CONEXUS_WEB_TRUSTED_RUNTIME = 'true'

$entry = Join-Path $ConexusRoot 'backend\dist\web-host.js'
if (-not (Test-Path $entry) -or -not (Test-Path (Join-Path $env:CONEXUS_WEB_DIST 'index.html'))) {
    throw "Conexus Web Host is not built. Run 'npm run build:web' and 'npm --prefix backend run build' in $ConexusRoot."
}
if (-not $env:CONEXUS_WEB_TOKEN) {
    $tokenBytes = New-Object byte[] 32
    $random = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try { $random.GetBytes($tokenBytes) } finally { $random.Dispose() }
    $env:CONEXUS_WEB_TOKEN = ([System.BitConverter]::ToString($tokenBytes)).Replace('-', '').ToLowerInvariant()
}

& node $entry
