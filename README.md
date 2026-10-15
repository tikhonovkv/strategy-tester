# Strategy Tester MTF — Мультитаймфреймовая платформа (Laravel + Timeweb Cloud App Platform)

Готовое веб-приложение на **Laravel** (с сохранением исходных классов `src/StrategyTester` и пакетом Python-исследований `python_research/`), полностью подготовленное для деплоя в **Timeweb Cloud App Platform** по инструкции [«Как развернуть приложение на Laravel»](https://timeweb.cloud/tutorials/cloud/kak-razvernut-prilozhenie-na-laravel).

---

## Быстрый деплой в Timeweb Cloud App Platform

1. Перейдите в панель **Timeweb Cloud → Apps → Создать** и выберите тип приложения **Laravel (PHP 8.2 / 8.3)**.
2. Подключите ваш аккаунт **GitHub** и выберите репозиторий **`tikhonovkv/strategy-tester`** (ветка `master`, переключатель **«Сборка по последнему коммиту»**).
3. В параметрах сборки укажите команду:
   ```bash
   php artisan db:seed --force
   ```
4. Нажмите **«Запустить деплой»**.
   - В репозитории уже предзаполнены:
     - файл окружения `.env` с `APP_KEY` и базой `database/database.sqlite`,
     - результаты всех Walk-Forward тестов в `storage/app/strategy_data.json`,
     - принудительная схема `URL::forceScheme('https')` в `app/Providers/AppServiceProvider.php` для работы за SSL-прокси Timeweb Cloud Apps.

---

## Страницы и эндпоинты приложения

| URL | Описание |
|---|---|
| `/` | Дашборд результатов Walk-Forward тестирования (`BTCUSDT` **+96.88%**, 5/5 полугодий в плюс; `ETHUSDT` **+89.80%**, 5/5 полугодий в плюс; ранние годы `BTCUSDT 2022–2024` **+62.21%**) |
| `/scanner?symbol=BTCUSDT` | Живой мультитаймфреймовый сканер Bybit Spot (`BTCUSDT`, `ETHUSDT`, `SOLUSDT`): расчёт `1h`, `4h` и `1d` RSI/SMA в реальном времени, фильтр перекупленности (`RSI > 70`) / перепроданности (`RSI < 30`) и готовый торговый план (лимитная цена, стоп-лосс, выход) |
| `/variants` | Полная таблица 24 вариантов входа и выхода с их вневыборочной корреляцией и средней доходностью на верхних сигналах |
| `/about` | Инструкция по использованию сигналов каждый час и ежемесячному Walk-Forward дообучению |
| `/api/scan/{symbol}` | JSON API живого сканера (`BTCUSDT`, `ETHUSDT`, `SOLUSDT`) для подключения внешних ботов и вебхуков |

---

## Python-скрипты обучения и тестирования (`python_research/`)

В папке `python_research/` находятся скрипты сборки 5-летнего мультитаймфреймового датасета (`70 108` размеченных часов `BTC + ETH`, 32 признака, 24 варианта входа и выхода) и двухголового ансамбля (`HistGradientBoostingRegressor` + `HistGradientBoostingClassifier`):

- `python_research/mtf_10variants_no_overbought_handoff.py` — 3-рукавный ансамбль с защитой от перекупленности дневного RSI (`+96.88%` на `BTCUSDT`, 5 из 5 полугодий выше нуля).
- `python_research/mtf_24variants_rich.py` и `python_research/mtf_24variants_pool.py` — сборка 5-летнего пула из 32 признаков и 24 вариантов сделок.
- `python_research/mtf_24variants_2slot.py` — двухслотовая система (основной трендовый слот + активный свинговый слот): `+89.02%` на `BTCUSDT` (5/5), `+89.80%` на `ETHUSDT` (5/5), `+62.21%` на ранних годах `BTCUSDT (2022–2024)`.
- `python_research/eth_mtf_10variants_check.py` и `python_research/btc_early_years_check.py` — скрипты кросс-проверки на другой монете и ранних годах.
