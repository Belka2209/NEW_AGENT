# Local Agent

Локальный AI-агент. Можно говорить в **браузере** или в **терминале**. Модель — Ollama или LM Studio на вашей видеокарте.

Под железо RDP-машины: **RTX 5060 Ti 16 ГБ**, 32 ГБ RAM, Windows.

## Что умеет

- Диалог со стримингом ответа (браузер и терминал)
- История чатов (SQLite)
- Файлы только внутри `workspace/`
- Команды терминала из `workspace/`
- Поиск в интернете без API-ключа (DuckDuckGo)
- Погода (Open-Meteo)
- Долгая память между чатами
- Локальные напоминания (без Telegram)
- Логи в `data/agent.log`
- Сообщения в Telegram через бота (по желанию)

## Чат в браузере

```powershell
.\run.ps1
```

или:

```powershell
.\.venv\Scripts\Activate.ps1
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Откройте [http://127.0.0.1:8000](http://127.0.0.1:8000)

Пока агент ищет или вызывает инструменты, в чате видно «Секунду, я думаю…», сырые JSON вызовов не показываются.

## Чат в терминале

Браузер и `uvicorn` не нужны. Должна быть запущена модель: **Ollama** или **LM Studio Local Server**.

```powershell
cd D:\MY_AGENT\NEW_AGENT
.\.venv\Scripts\Activate.ps1
python -m app.cli
```

или одной командой:

```powershell
.\chat.ps1
```

Пишете сообщение и Enter.

| Команда | Что делает |
|---|---|
| `/new` | новый диалог |
| `/help` | список команд |
| `/exit` или `/q` | выход |
| Ctrl+C | выход |

Пока идут инструменты, в терминале: «секунду, я думаю…», затем обычный ответ. История пишется в ту же SQLite, что и браузер (`data/agent.db`).

## Модель

По умолчанию: `qwen2.5-coder:14b` — комфортно для 16 ГБ VRAM, русский, код, вызов инструментов.

Альтернативы в `.env` (`OLLAMA_MODEL=`):

| Профиль | Модель | Когда ставить |
|---|---|---|
| Основной | `qwen2.5-coder:14b` или `qwen3-coder:14b` | повседневная работа |
| Быстрый | `qwen3.5:9b` / `qwen3:8b` | уже скачана в LM Studio или Ollama |
| Тяжёлый | Qwen 27B Q3/Q4 | только если `nvidia-smi` без offload на CPU |

Не ставьте reasoning-модели (QwQ, R1-distill) как основного агента.

Перед загрузкой модели:

```powershell
nvidia-smi
```

Нужны драйвер NVIDIA **570+** (Blackwell) и модель **целиком в VRAM**. Старый Xeon плохо тянет offload.

### Ollama

```powershell
ollama pull qwen3.5:9b
```

В `.env`:

```
OLLAMA_BASE_URL=http://127.0.0.1:11434
OLLAMA_MODEL=qwen3.5:9b
```

Модели лучше держать на диске D: переменная среды `OLLAMA_MODELS=D:\ollama\models`.

### LM Studio (модель уже скачана)

LM Studio должна быть **открыта**, модель загружена, Thinking выключен, Local Server запущен (порт 1234).

```
OLLAMA_BASE_URL=http://127.0.0.1:1234
OLLAMA_MODEL=имя_модели_из_LM_Studio
```

URL без `/v1`. Не держите одну и ту же карту сразу под Ollama 14B и LM Studio 9B — выгрузите одну модель (`ollama stop ...`).

Проверка: `curl http://127.0.0.1:1234/v1/models` — поле `id` это имя для `OLLAMA_MODEL`.

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

## Память, напоминания, логи

Долгая память живёт между чатами. Примеры: «запомни, что я работаю с диска D», «что ты обо мне помнишь?».

Напоминания только локальные, без Telegram. Примеры: «напомни сегодня в 18:00 проверить бэкап», «какие напоминания?», «закрой напоминание 1».

Агент сам подскажет просроченные в начале ответа.

Логи: `data/agent.log` (ротация ~2 МБ). На RDP:

```powershell
Get-Content D:\MY_AGENT\NEW_AGENT\data\agent.log -Tail 50
```

## Конфиг

Скопируйте `.env.example` в `.env`:

```
OLLAMA_BASE_URL=http://127.0.0.1:11434
OLLAMA_MODEL=qwen2.5-coder:14b
MAX_AGENT_STEPS=12
TERMINAL_TIMEOUT=45
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHATS=работа=-100123456789,личный=123456789
```

## Telegram-бот

Chrome не нужен. Агент пишет через Bot API.

1. В Telegram откройте `@BotFather` → `/newbot` → скопируйте токен.
2. В `.env` на RDP: `TELEGRAM_BOT_TOKEN=...`
3. Напишите боту `/start` в личке. Для группы: добавьте бота и отправьте в чат любое сообщение.
4. Перезапустите uvicorn, в агенте скажите: «покажи чаты телеграм».
5. Пропишите удобные имена в `TELEGRAM_CHATS=работа=-100...,личный=123...`

Дальше: «напиши в работу: сервер поднят».
