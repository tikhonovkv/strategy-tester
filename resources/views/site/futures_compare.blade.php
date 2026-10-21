@extends('layouts.app')

@section('title', 'Сверка Чемпионата Шварца vs Live')

@section('content')
<style>
    .kpi{display:flex;gap:14px;flex-wrap:wrap;margin:14px 0}
    .kpi .b{background:#0f1624;border:1px solid #222d45;border-radius:10px;padding:12px 16px;min-width:140px}
    .kpi .b .l{font-size:11px;color:#94a3b8;text-transform:uppercase;letter-spacing:.05em}
    .kpi .v{font-size:22px;font-weight:700;color:#fff;margin-top:4px}
    .ok{color:#10b981}.bad{color:#ef4444}.warn{color:#f59e0b}
    tr.matched td{background:rgba(16,185,129,0.12)}
    tr.warn td{background:rgba(245,158,11,0.15)}
    tr.bad td{background:rgba(239,68,68,0.25)}
    tr.missed td{background:rgba(239,68,68,0.09)}
</style>

<h1>Сверка: Чемпионат Шварца (Ultra 7x · Каскадный Тейк 3.0/5.5 ATR + Пирамида + Ротация)</h1>
@foreach ($compare['periods'] ?? [] as $p)
    <div class="card">
        <h2>{{ $p['label'] }} — +{{ number_format($p['return_pct'] ?? 0, 2) }}% ($2,000 → ${{ number_format($p['final_eq'] ?? 2000, 0) }})</h2>
        <div class="kpi">
            <div class="b"><div class="l">Сделок / Побед</div><div class="v ok">{{ $p['wins'] ?? 0 }} / {{ $p['total'] }} ({{ $p['win_rate'] ?? 0 }}%)</div></div>
            <div class="b"><div class="l">Рост периода</div><div class="v ok">+{{ number_format($p['return_pct'] ?? 0, 2) }}%</div></div>
            <div class="b"><div class="l">Макс. просадка</div><div class="v warn">{{ number_format($p['max_dd_pct'] ?? 0, 1) }}%</div></div>
            <div class="b"><div class="l">Совпало Live</div><div class="v ok">{{ $p['matched'] }}</div></div>
            <div class="b"><div class="l">Лишних</div><div class="v bad">{{ $p['extras'] }}</div></div>
        </div>
        <table>
            <tr><th>Вход</th><th>Выход</th><th>Пара</th><th>Сторона</th><th>Цена вх. → вых.</th><th>Маржа</th><th>Правило выхода</th><th>Статус</th><th>PnL</th><th>Баланс</th></tr>
        @foreach ($p['trades'] as $t)
        @php
            $pnl = $t['pnl_pct'] ?? 0;
            $rowCls = $pnl >= 0 ? 'matched' : 'missed';
        @endphp
        <tr class="{{ $rowCls }}">
            <td>{{ $t['time'] }}</td><td>{{ $t['exit_time'] ?? '—' }} ({{ $t['hold_h'] ?? 24 }}h)</td>
            <td><b>{{ $t['coin'] }}</b> {{ !empty($t['pyr']) ? '+PYR' : '' }}</td>
            <td>{{ ($t['side'] ?? 0) == 1 ? 'LONG' : 'SHORT' }}</td>
            <td>{{ $t['entry_price'] ?? '—' }} → {{ $t['exit_price'] ?? '—' }}</td>
            <td>${{ number_format($t['margin'] ?? 0, 0) }} ({{ $t['eff_lev'] ?? 7 }}x)</td>
            <td>{{ $t['reason'] ?? 'end' }}</td>
            <td>{{ ($t['status'] ?? '') === 'MATCHED' ? 'на счёте' : 'только бэктест' }}</td>
            <td><b>{{ $pnl >= 0 ? '+' : '' }}{{ number_format($pnl, 2) }}%</b> (${{ number_format($t['pnl_usd'] ?? 0, 0) }})</td>
            <td><b>${{ number_format($t['eq'] ?? 2000, 0) }}</b></td>
        </tr>
        @endforeach
        </table>
    </div>
@endforeach

@if (empty($compare['periods']))
    <p class="sub">Нет данных сверки. Запустите compare_live.py.</p>
@endif
@endsection
