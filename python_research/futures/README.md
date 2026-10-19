# Futures funding model (9-train / 4-trade)

Линейные USDT-перпетуалы Bybit (category=linear), лонг/шорт с плечом.
Обучается на 9 монетах, торгует 4 (ETH/SOL/ADA/SUI). funding + price -> 24h net PnL.

## Файлы
- futures_dataset.py — датасет (16 признаков) -> 24h net PnL. pairs_cache + fresh/.
- futures_9to4.py — бэктест (ежедневный ретрейн 180д, thr=0.6%, 3x, 20%). --recent-days=N.
- live_bot.py — ЖИВОЙ бот: сигналы + сверка счёта + ордера. --check / --dry-run / --live.
- compare_live.py — сверка бэктест vs live (MATCHED/MISSED/EXTRA + точность %).
- fetch_fresh.py — докачка свежих баров+funding в fresh/.

## Запуск
    python futures_dataset.py --no-oi
    python futures_9to4.py --recent-days=30 --leverage=3
    python live_bot.py --check
    python live_bot.py --live          # боевые ордера (осторожно!)
    python compare_live.py

## Ключи
Из ~/.cursor/bybit-top25-scan-state/bybit.env через rest.py (HMAC-SHA256). Не коммитить.

## Итоги (реинвест-бэктест, 5.7 лет, 2104 сделки)
2x +3852% | 3x +16514% | 4x +55844%. Реалистично (фикс. размер, slippage 0.3%): $2000 -> ~$10900 на 3x.


## Docker / Timeweb Cloud App Platform

Разворачивается на сервере через Docker Compose (ничего локально).

1. В панели Timeweb (App Platform → Docker Compose) подключи репозиторий.
2. В env панели задай:
   - `BYBIT_API_KEY` / `BYBIT_API_SECRET` — ключи Bybit;
   - `LIVE_MODE` — `0` (dry-run, по умолчанию) или `1` (реальные ордера);
   - `PORT` — порт, на который Timeweb роутит web-дашборд.
3. `futures-bot` — фоновый контейнер: `bootstrap.py` качает ~200 дней истории,
   `scheduler.py` каждые 8 часов (00:05/08:05/16:05 UTC) запускает `live_bot.py`.

Локально для теста:

    docker compose up -d futures-bot            # dry-run
    LIVE_MODE=1 docker compose up -d futures-bot  # live
