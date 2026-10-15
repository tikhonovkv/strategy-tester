@extends('layouts.app')

@section('title', 'Живой сканер Bybit — Strategy Tester MTF')

@section('content')
    <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:12px; margin-bottom:20px;">
        <div>
            <h1>Живой мультитаймфреймовый сканер Bybit ({{ $symbol }})</h1>
            <p class="sub" style="margin-bottom:0;">Расчёт индикаторов 1h, 4h и 1d по закрытым свечам Bybit Spot в реальном времени.</p>
        </div>
        <div style="display:flex; gap:8px;">
            <a href="{{ route('scanner', ['symbol' => 'BTCUSDT']) }}" class="btn {{ $symbol === 'BTCUSDT' ? '' : 'btn-outline' }}">BTCUSDT</a>
            <a href="{{ route('scanner', ['symbol' => 'ETHUSDT']) }}" class="btn {{ $symbol === 'ETHUSDT' ? '' : 'btn-outline' }}">ETHUSDT</a>
            <a href="{{ route('scanner', ['symbol' => 'SOLUSDT']) }}" class="btn {{ $symbol === 'SOLUSDT' ? '' : 'btn-outline' }}">SOLUSDT</a>
        </div>
    </div>

    @if(!($scan['ok'] ?? false))
        <div class="card">
            <h2 class="neg">Ошибка получения данных</h2>
            <p>{{ $scan['error'] ?? 'Неизвестная ошибка' }}</p>
        </div>
    @else
        @php $ind = $scan['indicators']; @endphp
        <div class="grid-4">
            <div class="card">
                <div class="stat-label">Закрытие 1h ({{ $scan['symbol'] }})</div>
                <div class="stat-val">${{ number_format($scan['price'], 2) }}</div>
                <div style="font-size:12px; color:var(--muted); margin-top:4px;">{{ $scan['timestamp_utc'] }}</div>
            </div>
            <div class="card">
                <div class="stat-label">Дневной тренд (1d SMA50)</div>
                <div class="stat-val {{ $ind['d1_dist50_pct'] >= 0 ? 'pos' : 'neg' }}">
                    {{ $ind['d1_dist50_pct'] >= 0 ? '+' : '' }}{{ $ind['d1_dist50_pct'] }}%
                </div>
                <div style="font-size:12px; color:var(--muted); margin-top:4px;">
                    SMA50: ${{ number_format($ind['d1_sma50'], 2) }} &middot; Наклон 5д: {{ $ind['d1_slope5d_pct'] >= 0 ? '+' : '' }}{{ $ind['d1_slope5d_pct'] }}%
                </div>
            </div>
            <div class="card">
                <div class="stat-label">RSI (1h / 4h / 1d)</div>
                <div class="stat-val {{ $ind['overbought_guard'] ? 'warn' : ($ind['oversold_guard'] ? 'warn' : 'pos') }}">
                    {{ $ind['h1_rsi14'] }} / {{ $ind['h4_rsi14'] }} / {{ $ind['d1_rsi14'] }}
                </div>
                <div style="font-size:12px; color:var(--muted); margin-top:4px;">
                    @if($ind['overbought_guard'])
                        Перекупленность (>70) — вход в лонг заблокирован
                    @elseif($ind['oversold_guard'])
                        Перепроданность (<30) — вход в шорт заблокирован
                    @else
                        Зона нормального импульса (30..70)
                    @endif
                </div>
            </div>
            <div class="card">
                <div class="stat-label">Среднесрочная 1d SMA20 & 4h SMA18</div>
                <div class="stat-val">${{ number_format($ind['d1_sma20'], 2) }}</div>
                <div style="font-size:12px; color:var(--muted); margin-top:4px;">
                    4h SMA18: ${{ number_format($ind['h4_sma18'], 2) }} &middot; 1h SMA24/72: {{ number_format($ind['h1_sma24'], 0) }}/{{ number_format($ind['h1_sma72'], 0) }}
                </div>
            </div>
        </div>

        <div class="card">
            <h2>Текущий режим: <span class="badge {{ str_contains($scan['regime'], 'ACTIVE') ? 'badge-green' : 'badge-amber' }}">{{ $scan['regime'] }}</span></h2>
            <p style="margin-bottom:16px;">{{ $scan['regime_title'] }}</p>

            @if($scan['recommended'])
                @php $rec = $scan['recommended']; @endphp
                <table>
                    <tbody>
                        <tr>
                            <th style="width:240px;">Выбранный вариант</th>
                            <td><strong>{{ $rec['variant'] }}</strong></td>
                        </tr>
                        <tr>
                            <th>Направление</th>
                            <td><span class="badge {{ str_contains($rec['side'], 'LONG') ? 'badge-green' : 'badge-red' }}">{{ $rec['side'] }}</span></td>
                        </tr>
                        <tr>
                            <th>Лимитная цена входа</th>
                            <td><strong>${{ number_format($rec['limit_price'], 2) }}</strong></td>
                        </tr>
                        <tr>
                            <th>Структурный стоп-лосс</th>
                            <td class="neg"><strong>${{ number_format($rec['stop_price'], 2) }}</strong></td>
                        </tr>
                        <tr>
                            <th>Правило выхода / Тейк</th>
                            <td class="pos">{{ $rec['target_price'] }}</td>
                        </tr>
                        <tr>
                            <th>Отмена лимитного ордера</th>
                            <td>{{ $rec['cancel_rule'] }}</td>
                        </tr>
                    </tbody>
                </table>
            @else
                <p style="color:var(--muted);">На текущем часе новые лимитные заявки не выставляются. Капитал остаётся в USDT до выравнивания 1d SMA50, 4h SMA18 и 1h SMA24/SMA72 и снятия перекупленности/перепроданности.</p>
            @endif
        </div>
    @endif
@endsection
