#requires -Version 7
[CmdletBinding()]
param(
    [string[]]$Paths = @('src', 'tests'),
    [string]$BannedPackages = 'ApplicationInsights|Sentry|Segment|Mixpanel|Datadog|NewRelic|Raygun|Rollbar|Bugsnag|HockeyApp|AppCenter|GoogleAnalytics'
)
$ErrorActionPreference = 'Stop'
$fail = $false

$patterns = @(
    'InstrumentationKey=' ; 'https://[a-f0-9]+@[a-z0-9]+\.ingest\.sentry\.io'
    'AKIA[0-9A-Z]{16}' ; 'ASIA[0-9A-Z]{16}' ; 'xoxb-[0-9]+-[0-9]+-[a-zA-Z0-9]+'
    'sk_live_[0-9a-zA-Z]+' ; 'sk-ant-[0-9a-zA-Z_-]+' ; 'ghp_[0-9a-zA-Z]{36,}'
    '-----BEGIN (RSA|OPENSSH|EC|PGP) PRIVATE KEY-----'
) -join '|'

foreach ($p in $Paths) {
    if (-not (Test-Path $p)) { continue }
    $hits = Select-String -Path (Get-ChildItem $p -Recurse -File).FullName -Pattern $patterns -AllMatches 2>$null
    if ($hits) {
        Write-Host "::error::Secret-shape:" -ForegroundColor Red
        $hits | ForEach-Object { Write-Host "  $($_.Path):$($_.LineNumber): $($_.Line)" }
        $fail = $true
    }
}

Get-ChildItem -Recurse -File -Include 'pyproject.toml','requirements*.txt' -ErrorAction SilentlyContinue | ForEach-Object {
    if ((Get-Content $_.FullName -Raw) -match $BannedPackages) {
        Write-Host "::error::Banned package in $($_.FullName)" -ForegroundColor Red
        $fail = $true
    }
}

if ($fail) { exit 1 }
Write-Host 'OK.' -ForegroundColor Green
