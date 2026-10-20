@extends('layouts.app')

@section('title', 'Сверка бэктест vs live')

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
    tr.missed td{background:rgba(239,68,68,0.12)}
</style>

<h1>Сверка: бэктест модели vs живой счёт</h1>
@foreach ($compare['periods'] ?? [] as $p)
    <div class="card">
        <h2>{{ $p['label'] }}</h2>
        <div class="kpi">
            <div class="b"><div class="l">Сигналов</div><div class="v">{{ $p['total'] }}</div></div>
            <div class="b"><div class="l">Совпало</div><div class="v ok">{{ $p['matched'] }}</div></div>
            <div class="b"><div class="l">Пропущено</div><div class="v warn">{{ $p['missed'] }}</div></div>
            <div class="b"><div class="l">Лишних</div><div class="v bad">{{ $p['extras'] }}</div></div>
            <div class="b"><div class="l">Точность</div><div class="v {{ ($p['fidelity'] ?? 0) >= 90 ? 'ok' : (($p['fidelity'] ?? 0) >= 50 ? 'warn' : 'bad') }}">{{ $p['fidelity'] }}%</div></div>
            <div class="b"><div class="l">PnL модели</div><div class="v">{{ number_format($p['pnl_sum'] ?? 0, 2) }}%</div></div>
        </div>
        <table>
                    <tr><th>Вход</th><th>Выход</th><th>Монета</th><th>Сторона</th><th>Цена вх.</th><th>Цена вых.</th><th>Объём</th><th>Статус</th><th>Расхождение</th><th>PnL</th></tr>
        @foreach ($p['trades'] as $t)
        @php
            $q = $t['quality'] ?? 'OK';
            $rowCls = strtolower($t['status'] ?? '');
            if (($t['status'] ?? '') === 'MATCHED' && $q !== 'OK') { $rowCls .= $q === 'PRICE_QTY' ? ' bad' : ' warn'; }
            $disc = '';
            if (($t['status'] ?? '') === 'MATCHED') {
                $parts = [];
                if (isset($t['price_diff_pct']) && $t['price_diff_pct'] > 0.5) { $parts[] = 'цена ' . $t['price_diff_pct'] . '%'; }
                if (isset($t['qty_diff_pct']) && $t['qty_diff_pct'] > 15) { $parts[] = 'объём ' . $t['qty_diff_pct'] . '%'; }
                $disc = $parts ? implode(' / ', $parts) : 'OK';
            }
        @endphp
        <tr class="{{ $rowCls }}">
            <td>{{ $t['time'] }}</td><td>{{ $t['exit_time'] ?? '—' }}</td><td>{{ $t['coin'] }}</td>
            <td>{{ ($t['side'] ?? 0) == 1 ? 'LONG' : 'SHORT' }}</td>
            <td>{{ $t['entry_price'] ?? '—' }}</td><td>{{ $t['exit_price'] ?? '—' }}</td><td>{{ $t['qty'] ?? '—' }}</td>
            <td>{{ $t['status'] }}</td><td>{{ $disc ?: '—' }}</td><td>{{ number_format($t['pnl_pct'] ?? 0, 2) }}%</td>
        </tr>
        @endforeach
        </table>
    </div>
@endforeach

@if (empty($compare['periods']))
    <p class="sub">Нет данных сверки. Запустите compare_live.py.</p>
@endif
@endsection
