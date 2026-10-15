@extends('layouts.app')

@section('title', 'Автономный почасовой бот & Переменные окружения — Strategy Tester MTF')

@section('content')
    <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:12px; margin-bottom:20px;">
        <div>
            <h1>Автономный почасовой сканер Bybit Spot + Telegram (ENV)</h1>
            <p class="sub" style="margin-bottom:0;">
                Каждый час собирает закрытые свечи 1h / 4h / 1d по всем мониторинговым монетам и отправляет задачи на открытие сделок в Telegram. Переменные задаются строго через окружение (ENV).
            </p>
        </div>
        <form method="POST" action="{{ route('autonomous.run') }}">
            @csrf
            <button type="submit" class="btn btn-green">Запустить полный обход и отправить в Telegram сейчас</button>
        </form>
    </div>

    @if(session('status'))
        <div class="alert">{{ session('status') }}</div>
    @endif

    <div class="grid-4">
        <div class="card">
            <div class="stat-label">Статус Telegram (из ENV)</div>
            <div class="stat-val {{ $config['configured'] ? 'pos' : 'warn' }}" style="font-size:22px;">
                {{ $config['configured'] ? 'ПОДКЛЮЧЁН' : 'ЖДЁТ ENV ПЕРЕМЕННЫХ' }}
            </div>
            <div style="font-size:12px; color:var(--muted); margin-top:4px;">
                Токен: {{ $config['token_masked'] }} &middot; Chat ID: {{ $config['chat_id'] ?: 'не задан' }}
            </div>
        </div>
        <div class="card">
            <div class="stat-label">Последний почасовой обход</div>
            <div class="stat-val" style="font-size:20px;">{{ $state['last_hour_key'] ?? 'Ещё не запускался' }}</div>
            <div style="font-size:12px; color:var(--muted); margin-top:4px;">
                Время: {{ $state['last_run_utc'] ?? '—' }}
            </div>
        </div>
        <div class="card">
            <div class="stat-label">Мониторинговых монет</div>
            <div class="stat-val">{{ count($config['symbols']) }}</div>
            <div style="font-size:12px; color:var(--muted); margin-top:4px;">
                Автономный демон: {{ $state['daemon_heartbeat_utc'] ? 'активен (' . $state['daemon_heartbeat_utc'] . ')' : 'активен' }}
            </div>
        </div>
        <div class="card">
            <div class="stat-label">Активные задачи / позиции</div>
            <div class="stat-val pos">{{ count($state['active_tasks'] ?? []) }}</div>
            <div style="font-size:12px; color:var(--muted); margin-top:4px;">
                Режим: {{ $config['notify_every_hour'] ? 'Отчёт каждый час + сигналы' : 'Только при новых задачах' }}
            </div>
        </div>
    </div>

    <div class="card">
        <h2>Переменные окружения (задаются в панели Timeweb Cloud &rarr; Настройки &rarr; Переменные окружения или в <code>.env</code>)</h2>
        <table>
            <thead>
                <tr>
                    <th>Имя переменной в ENV</th>
                    <th>Текущее значение в окружении</th>
                    <th>Назначение</th>
                </tr>
            </thead>
            <tbody>
                <tr>
                    <td><code>TELEGRAM_BOT_TOKEN</code></td>
                    <td>
                        <span class="badge {{ $config['token'] !== '' ? 'badge-green' : 'badge-amber' }}">
                            {{ $config['token_masked'] }}
                        </span>
                    </td>
                    <td>Токен вашего Telegram-бота от <code>@BotFather</code> для отправки задач на открытие сделок</td>
                </tr>
                <tr>
                    <td><code>TELEGRAM_CHAT_ID</code></td>
                    <td>
                        <span class="badge {{ $config['chat_id'] !== '' ? 'badge-green' : 'badge-amber' }}">
                            {{ $config['chat_id'] ?: 'не задан (TELEGRAM_CHAT_ID)' }}
                        </span>
                    </td>
                    <td>ID вашего чата, группы или канала в Telegram (например <code>345172024</code> или <code>-100...</code>)</td>
                </tr>
                <tr>
                    <td><code>SCAN_SYMBOLS</code></td>
                    <td><code>{{ implode(',', $config['symbols']) }}</code></td>
                    <td>Список мониторинговых спотовых пар Bybit через запятую (по умолчанию 10 монет)</td>
                </tr>
                <tr>
                    <td><code>TELEGRAM_NOTIFY_EVERY_HOUR</code></td>
                    <td><code>{{ $config['notify_every_hour'] ? 'true' : 'false' }}</code></td>
                    <td><code>true</code> — присылать почасовую сводку + сигналы; <code>false</code> — присылать только при появлении сделки</td>
                </tr>
                <tr>
                    <td><code>TELEGRAM_BASE_URL</code></td>
                    <td><code>{{ $config['base_url'] }}</code></td>
                    <td>Базовый адрес Telegram Bot API (по умолчанию <code>https://api.telegram.org</code>)</td>
                </tr>
            </tbody>
        </table>
    </div>

    @if(!empty($state['active_tasks']))
        <div class="card">
            <h2>Активные задачи на открытие / сопровождение сделок ({{ count($state['active_tasks']) }})</h2>
            <table>
                <thead>
                    <tr>
                        <th>Монета</th>
                        <th>Статус</th>
                        <th>Сторона</th>
                        <th>Вариант</th>
                        <th>Лимит входа</th>
                        <th>Стоп-лосс</th>
                        <th>Выход / Цель</th>
                        <th>Создана (UTC)</th>
                    </tr>
                </thead>
                <tbody>
                    @foreach($state['active_tasks'] as $sym => $t)
                        <tr>
                            <td><strong>{{ $sym }}</strong></td>
                            <td>
                                <span class="badge {{ $t['status'] === 'ACTIVE_POSITION' ? 'badge-green' : 'badge-amber' }}">
                                    {{ $t['status'] }}
                                </span>
                            </td>
                            <td>
                                <span class="badge {{ str_contains($t['side'], 'LONG') ? 'badge-green' : 'badge-red' }}">
                                    {{ $t['side'] }}
                                </span>
                            </td>
                            <td><code>{{ $t['variant'] }}</code></td>
                            <td><strong>${{ $t['limit_price'] }}</strong></td>
                            <td class="neg"><strong>${{ $t['stop_price'] }}</strong></td>
                            <td class="pos">{{ $t['target_price'] }}</td>
                            <td style="font-size:12px; color:var(--muted);">{{ $t['created_at_utc'] }}</td>
                        </tr>
                    @endforeach
                </tbody>
            </table>
        </div>
    @endif

    <div class="card">
        <h2>Сводная таблица последнего почасового обхода по всем мониторинговым монетам</h2>
        <table>
            <thead>
                <tr>
                    <th>Монета</th>
                    <th>Закрытие 1h</th>
                    <th>Режим MTF</th>
                    <th>RSI (1h / 4h / 1d)</th>
                    <th>1d SMA50 (наклон 5д)</th>
                    <th>4h SMA18</th>
                    <th>1h SMA24 / SMA72</th>
                    <th>Задача на текущий час</th>
                </tr>
            </thead>
            <tbody>
                @foreach(($state['latest_results'] ?? []) as $sym => $r)
                    @if(!($r['ok'] ?? false))
                        <tr>
                            <td><strong>{{ $sym }}</strong></td>
                            <td colspan="7" class="neg">{{ $r['error'] ?? 'Ошибка API' }}</td>
                        </tr>
                    @else
                        @php $ind = $r['indicators']; $rec = $r['recommended'] ?? null; @endphp
                        <tr>
                            <td>
                                <a href="{{ route('scanner', ['symbol' => $sym]) }}" style="color:#fff; font-weight:700; text-decoration:none;">
                                    {{ $sym }}
                                </a>
                            </td>
                            <td><strong>${{ $r['price'] }}</strong></td>
                            <td>
                                <span class="badge {{ str_contains($r['regime'], 'ACTIVE') ? 'badge-green' : (str_contains($r['regime'], 'COOLDOWN') ? 'badge-red' : 'badge-blue') }}">
                                    {{ $r['regime'] }}
                                </span>
                            </td>
                            <td>
                                <span class="{{ $ind['overbought_guard'] || $ind['oversold_guard'] ? 'warn' : '' }}">
                                    {{ $ind['h1_rsi14'] }} / {{ $ind['h4_rsi14'] }} / {{ $ind['d1_rsi14'] }}
                                </span>
                            </td>
                            <td class="{{ $ind['d1_dist50_pct'] >= 0 ? 'pos' : 'neg' }}">
                                ${{ $ind['d1_sma50'] }} ({{ $ind['d1_dist50_pct'] >= 0 ? '+' : '' }}{{ $ind['d1_dist50_pct'] }}%, наклон {{ $ind['d1_slope5d_pct'] >= 0 ? '+' : '' }}{{ $ind['d1_slope5d_pct'] }}%)
                            </td>
                            <td>${{ $ind['h4_sma18'] }}</td>
                            <td>{{ $ind['h1_sma24'] }} / {{ $ind['h1_sma72'] }}</td>
                            <td>
                                @if($rec)
                                    <span class="badge {{ str_contains($rec['side'], 'LONG') ? 'badge-green' : 'badge-red' }}">
                                        {{ $rec['side'] }} @ ${{ $rec['limit_price'] }} (SL ${{ $rec['stop_price'] }})
                                    </span>
                                @else
                                    <span style="color:var(--muted); font-size:13px;">Ожидание (Кэш)</span>
                                @endif
                            </td>
                        </tr>
                    @endif
                @endforeach
            </tbody>
        </table>
    </div>
@endsection