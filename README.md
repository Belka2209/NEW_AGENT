# Local Agent

Локальный AI-агент с чатом в браузере. Backend — FastAPI, модель — Ollama на вашей видеокарте.

Под железо RDP-машины: **RTX 5060 Ti 16 ГБ**, 32 ГБ RAM, Windows.

## Что умеет

- Диалог со стримингом ответа
- История чатов (SQLite)
- Файлы только внутри `workspace/`
- Команды терминала из `workspace/`
- Поиск в интернете без API-ключа (DuckDuckGo)

Откройте после запуска: [http://127.0.0.1:8000](http://127.0.0.1:8000)

## Модель

По умолчанию: `qwen2.5-coder:14b` — комфортно для 16 ГБ VRAM, русский, код, вызов инструментов.

Альтернативы в `.env` (`OLLAMA_MODEL=`):

| Профиль | Модель | Когда ставить |
|---|---|---|
| Основной | `qwen2.5-coder:14b` или `qwen3-coder:14b` | повседневная работа |
| Быстрый | `qwen2.5:7b` / `qwen3:8b` | нужна скорость |
| Тяжёлый | Qwen 27B Q3/Q4 | только если `nvidia-smi` без offload на CPU |

Не ставьте reasoning-модели (QwQ, R1-distill) как основного агента.

Перед загрузкой модели:

```powershell
nvidia-smi
```

Нужны драйвер NVIDIA **570+** (Blackwell) и модель **целиком в VRAM**. Старый Xeon плохо тянет offload.

## Установка на Windows (RDP)

Нужны Git, Python 3.11+, драйвер NVIDIA 570+, [Ollama](https://ollama.com/download/windows).

```powershell
git clone git@github.com:Belka2209/NEW_AGENT.git
cd NEW_AGENT
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
ollama pull qwen2.5-coder:14b
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Или `.\run.ps1` из корня репозитория.

Docker в этой версии не используем: Ollama с CUDA на Windows надёжнее ставить нативно.

## Как обновлять с этой машины

Здесь: правки → коммит → `git push`.

На RDP:

```powershell
git pull
# перезапустить uvicorn
```

Модели Ollama и файл `.env` в git не входят.

## Конфиг

Скопируйте `.env.example` в `.env`:

```
OLLAMA_BASE_URL=http://127.0.0.1:11434
OLLAMA_MODEL=qwen2.5-coder:14b
MAX_AGENT_STEPS=12
TERMINAL_TIMEOUT=45
```
