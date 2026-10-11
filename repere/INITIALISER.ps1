$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
if (-not (Test-Path '.env')) {
    $bytes = New-Object byte[] 32
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    $rng.GetBytes($bytes)
    $rng.Dispose()
    $password = ([System.BitConverter]::ToString($bytes)).Replace('-', '').ToLowerInvariant()
    $config = "POSTGRES_PASSWORD=$password`nGEMINI_API_KEY=`nGEMINI_MODEL=gemini-3.5-flash-lite`n"
    [System.IO.File]::WriteAllText((Join-Path $PSScriptRoot '.env'), $config, (New-Object System.Text.UTF8Encoding($false)))
    Write-Host 'Configuration locale creee.'
} else {
    Write-Host 'Configuration existante conservee.'
}
