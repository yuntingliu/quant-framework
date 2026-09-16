param(
    [string]$ConexusRoot = (Join-Path (Split-Path $PSScriptRoot -Parent) '..\Conexus'),
    [string]$ProjectRoot = (Split-Path $PSScriptRoot -Parent),
    [string]$EnvFile,
    [ValidateRange(1, 65535)][int]$Port = 3000,
    [switch]$TrustedRuntime,
    [switch]$Check
)

$ErrorActionPreference = 'Stop'
$ConexusRoot = (Resolve-Path -LiteralPath $ConexusRoot).Path
$ProjectRoot = (Resolve-Path -LiteralPath $ProjectRoot).Path

# Explicit environment files contain JSON-quoted values, like AlphaLab's service
# configuration. Existing process variables take precedence. Never print values.
if ($EnvFile) {
    foreach ($line in Get-Content -LiteralPath $EnvFile) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith('#')) { continue }
        if ($trimmed -notmatch '^([A-Za-z_][A-Za-z0-9_]*)=(.*)$') {
            throw 'Invalid environment entry; expected NAME="JSON-quoted value".'
        }
        $name = $Matches[1]
        try { $value = ConvertFrom-Json -InputObject $Matches[2] } catch {
            throw "Invalid JSON-quoted environment value for $name."
        }
        if ($value -isnot [string]) { throw "Environment value for $name must be a string." }
        if (-not [Environment]::GetEnvironmentVariable($name, 'Process')) {
            [Environment]::SetEnvironmentVariable($name, $value, 'Process')
        }
    }
}

$layouts = @(
    @{ Name = 'workspace'; Entry = 'apps\web\server-dist\apps\web\server\entrypoints\web-host.js'; Dist = 'apps\web\dist' },
    @{ Name = 'legacy'; Entry = 'backend\dist\web-host.js'; Dist = 'dist\web' }
)
$layout = $layouts | Where-Object {
    (Test-Path -LiteralPath (Join-Path $ConexusRoot $_.Entry) -PathType Leaf) -and
    (Test-Path -LiteralPath (Join-Path $ConexusRoot (Join-Path $_.Dist 'index.html')) -PathType Leaf)
} | Select-Object -First 1
if (-not $layout) {
    throw 'No supported Conexus Web Host build found. Build the checkout using its own instructions; a desktop-only build cannot serve AlphaLab.'
}
$nodeVersion = & node -p 'process.versions.node'
if ($LASTEXITCODE -ne 0 -or [version]$nodeVersion -lt [version]'22.12.0') {
    throw 'Conexus Web Host requires Node.js 22.12 or later.'
}
if (-not $env:CONEXUS_WEB_TOKEN -or $env:CONEXUS_WEB_TOKEN -cnotmatch '^[A-Za-z0-9_-]{16,512}$') {
    throw 'Set a persistent CONEXUS_WEB_TOKEN (16-512 URL-safe characters) in the process or -EnvFile. This is the administrator token, not the publication service credential.'
}

$env:NODE_ENV = 'production'
$env:CONEXUS_PROJECT_ROOT = $ProjectRoot
$env:CONEXUS_WEB_DIST = Join-Path $ConexusRoot $layout.Dist
$env:CONEXUS_WEB_HOST = '127.0.0.1'
$env:CONEXUS_WEB_PORT = [string]$Port
if (-not $env:CONEXUS_SECRET_ROOT) {
    $env:CONEXUS_SECRET_ROOT = Join-Path $ConexusRoot '.conexus-web\secrets'
}
if ($TrustedRuntime) { $env:CONEXUS_WEB_TRUSTED_RUNTIME = 'true' }
$entry = Join-Path $ConexusRoot $layout.Entry
if ($Check) {
    [ordered]@{
        layout = $layout.Name
        entry = $entry
        projectRoot = $ProjectRoot
        nodeVersion = $nodeVersion
        administratorTokenConfigured = $true
        trustedRuntime = ($env:CONEXUS_WEB_TRUSTED_RUNTIME -eq 'true')
        check = 'files_and_configuration_only'
    } | ConvertTo-Json
    exit 0
}

Push-Location -LiteralPath $ConexusRoot
try { & node $entry; $serverExitCode = $LASTEXITCODE } finally { Pop-Location }
exit $serverExitCode
