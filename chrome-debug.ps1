$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$profile = Join-Path $root "data\chrome-profile"
New-Item -ItemType Directory -Force -Path $profile | Out-Null

$chrome = @(
    "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
    "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
    "$env:LocalAppData\Google\Chrome\Application\chrome.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1

if (-not $chrome) {
    Write-Error "Chrome не найден. Установите Google Chrome."
    exit 1
}

Write-Host "Отдельный профиль агента: $profile"
Write-Host "Обычный Chrome можно не закрывать."
Start-Process -FilePath $chrome -ArgumentList @(
    "--remote-debugging-port=9222",
    "--user-data-dir=$profile",
    "--remote-allow-origins=*",
    "about:blank"
)
Write-Host "В этом окне войдите в Bitrix/hh.ru один раз — сессия сохранится."
