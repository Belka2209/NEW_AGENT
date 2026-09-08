if (-not (Test-Path .venv)) {
    python -m venv .venv
}
& .\.venv\Scripts\Activate.ps1
if (-not (Test-Path .env)) {
    Copy-Item .env.example .env
}
python -m app.cli
