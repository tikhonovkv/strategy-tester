@extends('layouts.app')

@section('title', 'Как развернуть, использовать и дообучать — Strategy Tester MTF')

@section('content')
    <h1>Развёртывание в Timeweb Cloud App Platform и дообучение</h1>
    <p class="sub">Пошаговая инструкция по деплою из GitHub и ежемесячному Walk-Forward переобучению.</p>

    <div class="card">
        <h2>1. Развёртывание в Timeweb Cloud App Platform (по инструкции Laravel)</h2>
        <ol style="padding-left:20px; color:var(--text);">
            <li style="margin-bottom:8px;">Зайдите в панель <strong>Timeweb Cloud → Apps → Создать</strong> и выберите тип приложения <strong>Laravel (PHP 8.2 / 8.3)</strong>.</li>
            <li style="margin-bottom:8px;">Подключите ваш аккаунт GitHub и выберите репозиторий <code>tikhonovkv/strategy-tester</code> (ветка <code>master</code>, переключатель «Сборка по последнему коммиту»).</li>
            <li style="margin-bottom:8px;">В поле команды сборки можно указать <code>php artisan db:seed --force</code> (или оставить по умолчанию — предзаполненные результаты уже лежат в репозитории).</li>
            <li style="margin-bottom:8px;">В <code>app/Providers/AppServiceProvider.php</code> уже включён <code>URL::forceScheme('https')</code> для корректной работы SSL в Timeweb Cloud Apps.</li>
        </ol>
    </div>

    <div class="card">
        <h2>2. Как использовать сигналы каждый час</h2>
        <p style="margin-bottom:10px;">
            Откройте вкладку <strong>«Живой сканер Bybit»</strong> или вызовите JSON-эндпоинт:
            <code>/api/scan/BTCUSDT</code> или <code>/api/scan/ETHUSDT</code>.
        </p>
        <p style="color:var(--muted);">
            Сканер автоматически загружает последние закрытые свечи <code>1h</code>, <code>4h</code> и <code>1d</code>, проверяет условие двухдневного удержания над/под <code>1d SMA50</code>, наклон за 5 дней и блокировку перекупленности (<code>RSI > 70</code>) / перепроданности (<code>RSI < 30</code>), после чего возвращает точную цену лимитки, структурного стопа и правило выхода.
        </p>
    </div>

    <div class="card">
        <h2>3. Как дообучать Python-модели (раз в месяц)</h2>
        <p style="margin-bottom:10px;">В папке <code>python_research/</code> лежат скрипты сборки 24-вариантного датасета и двухголового ансамбля:</p>
        <pre style="background:#090d16; padding:14px; border-radius:8px; overflow-x:auto; color:#93c5fd; font-size:13px;">python -u python_research/mtf_24variants_pool.py
python -u python_research/mtf_24variants_2slot.py</pre>
        <p style="color:var(--muted); margin-top:10px;">
            Скрипты обучают <code>HistGradientBoostingRegressor</code> и <code>HistGradientBoostingClassifier</code> строго на тех часах, чьи сделки уже закрылись до окна валидации (<code>exits <= val_start</code>), и сохраняют обновлённый JSON-отчёт.
        </p>
    </div>
@endsection
