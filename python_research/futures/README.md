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

## Честный дневной прогон модели
1907 отдельных обучений, по одному на каждый день с 2021-07-31 по 2026-10-19. Окно 180 дней, в выборку входят только сделки, у которых 24 часа уже закрылись. После закрытого убытка вес следующих примеров этой монеты растёт (до ×3). Порог 0.6%, плечо 3x, 20% депозита, монеты ETH/SOL/ADA/SUI.

Итог `stable_causal_full.json`: $2000 → $25.25 (**−98.74%**), CAGR **−56.7%**, просадка 99.2%, 2374 сделки, 0 пропущенных дней, 67 минут счёта.


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
