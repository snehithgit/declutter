# Run as administrator. This changes only the dedicated PhotoSift firewall rule.
param(
    [string]$ZimaAddress = '192.168.68.63',
    [string]$OllamaPath = "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe",
    [string]$ResultPath = ''
)
$ErrorActionPreference = 'Stop'
try {
    $parsedAddress = $null
    if (-not [Net.IPAddress]::TryParse($ZimaAddress, [ref]$parsedAddress)) {
        throw 'ZimaAddress must be a literal IP address.'
    }
    if (-not (Test-Path -LiteralPath $OllamaPath -PathType Leaf)) {
        throw 'Ollama executable was not found.'
    }
    $ruleName = 'PhotoSift-Ollama-Zima'
    $existing = Get-NetFirewallRule -Name $ruleName -ErrorAction SilentlyContinue
    if ($existing) {
        $existing | Remove-NetFirewallRule
    }
    New-NetFirewallRule -Name $ruleName -DisplayName 'PhotoSift - Ollama from Zima only' `
        -Direction Inbound -Action Allow -Enabled True -Profile Any -Protocol TCP `
        -LocalPort 11434 -RemoteAddress $ZimaAddress -Program $OllamaPath `
        -EdgeTraversalPolicy Block | Out-Null
    $result = @{ success = $true; remote_address = $ZimaAddress; port = 11434 }
} catch {
    $result = @{ success = $false; error = $_.Exception.Message }
}
if ($ResultPath) {
    $result | ConvertTo-Json | Set-Content -LiteralPath $ResultPath -Encoding UTF8
}
if (-not $result.success) {
    Write-Error $result.error
    exit 1
}
