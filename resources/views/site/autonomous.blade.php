@extends('layouts.app')

@section('title', 'Автономный почасовой бот & Telegram — Strategy Tester MTF')

@section('content')
    <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:12px; margin-bottom:20px;">
        <div>
            <h1>Автономный почасовой сканер Bybit Spot + Telegram</h1>
            <p class="sub" style="margin-bottom:0;">
                Каждый час собирает закрытые свечи 1h / 4h / 1d по всем монетам, проверяет 24 варианта и отправляет задачи на открытие сделок в Telegram.
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
            <div class="stat-label">Статус Telegram-бота</div>
            <div class="stat-val {{ $config['configured'] ? 'pos' : 'warn' }}" style="font-size:22px;">
                {{ $config['configured'] ? 'ПОДКЛЮЧЁН' : 'НЕ НАСТРОЕН' }}
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
            <div class="stat-label">Отслеживаемых монет</div>
            <div class="stat-val">{{ count($config['symbols']) }}</div>
            <div style="font-size:12px; color:var(--muted); margin-top:4px;">
                Автономный демон: {{ $state['daemon_heartbeat_utc'] ? 'активен (' . $state['daemon_heartbeat_utc'] . ')' : 'ожидание' }}
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
        <h2>Сводная таблица последнего почасового обхода по всем монетам</h2>
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

    <div class="card">
        <h2>Настройки Telegram и списка монет для автономного обхода</h2>
        <p style="font-size:13px; color:var(--muted); margin-bottom:16px;">
            Укажите токен вашего Telegram-бота (от <code>@BotFather</code>) и ваш <code>Chat ID</code> (или задайте переменные окружения <code>TELEGRAM_BOT_TOKEN</code> и <code>TELEGRAM_CHAT_ID</code> в панели Timeweb Cloud).
        </p>
        <form method="POST" action="{{ route('autonomous.settings') }}">
            @csrf
            <div style="display:grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap:16px; margin-bottom:16px;">
                <div>
                    <label style="display:block; font-size:13px; color:var(--muted); margin-bottom:6px;">Telegram Bot Token</label>
                    <input type="password" name="telegram_bot_token" placeholder="{{ $config['configured'] ? 'Оставьте пустым, чтобы не менять (' . $config['token_masked'] . ')' : '123456789:AAH...' }}">
                </div>
                <div>
                    <label style="display:block; font-size:13px; color:var(--muted); margin-bottom:6px;">Telegram Chat ID (ID чата или канала)</label>
                    <input type="text" name="telegram_chat_id" value="{{ $config['chat_id'] }}" placeholder="Например: 345172024 или -100123456789">
                </div>
                <div>
                    <label style="display:block; font-size:13px; color:var(--muted); margin-bottom:6px;">Список монет Bybit Spot (через запятую)</label>
                    <input type="text" name="symbols" value="{{ implode(',', $config['symbols']) }}">
                </div>
            </div>
            <div style="margin-bottom:16px;">
                <label style="display:inline-flex; align-items:center; gap:8px; cursor:pointer; font-size:14px;">
                    <input type="checkbox" name="notify_every_hour" value="1" {{ $config['notify_every_hour'] ? 'checked' : '' }}>
                    Отправлять сводку в Telegram каждый час (если выключено — отправляет только при появлении новой задачи на сделку или срабатывании ордера)
                </label>
            </div>
            <div style="display:flex; gap:12px; flex-wrap:wrap;">
                <button type="submit" name="action" value="save_and_scan" class="btn btn-green">
                    Сохранить и сразу отправить тестовый отчёт в Telegram
                </button>
                <button type="submit" name="action" value="save_only" class="btn btn-outline">
                    Только сохранить настройки
                </button>
            </div>
        </form>
    </div>
@endsection
