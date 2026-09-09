$chrome = @(
    "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
    "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
    "$env:LocalAppData\Google\Chrome\Application\chrome.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1

if (-not $chrome) {
    Write-Error "Chrome не найден. Установите Google Chrome."
    exit 1
}

Write-Host "Закройте все окна Chrome, если они уже открыты — иначе порт 9222 не подхватится."
Write-Host "Запуск: $chrome --remote-debugging-port=9222"
Start-Process -FilePath $chrome -ArgumentList "--remote-debugging-port=9222"
Write-Host "Дальше откройте Bitrix или hh.ru и войдите в учётку. Агенту скажите: «посмотри вкладки браузера»."
