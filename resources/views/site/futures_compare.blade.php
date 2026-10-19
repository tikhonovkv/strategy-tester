@extends('layouts.app')

@section('content')
<h1>Сверка: бэктест модели vs живой счёт</h1>
@foreach ($compare['periods'] ?? [] as $p)
    <h2>{{ $p['label'] ?? '' }}</h2>
    <p>
        Сигналов модели: {{ $p['total'] ?? 0 }} ·
        Совпало: <b>{{ $p['matched'] ?? 0 }}</b> ·
        Пропущено: {{ $p['missed'] ?? 0 }} ·
        Лишних живых: {{ $p['extras'] ?? 0 }} ·
        Точность следования: <b>{{ ($p['fidelity'] ?? 0) }}%</b> ·
        PnL модели: {{ number_format($p['pnl_sum'] ?? 0, 2) }}%
    </p>
    <table>
        <tr><th>Время</th><th>Монета</th><th>Сторона</th><th>Статус</th><th>PnL</th></tr>
        @foreach ($p['trades'] ?? [] as $t)
        <tr style="{{ ($t['status'] ?? '') === 'MATCHED' ? 'background:#eafaf0' : 'background:#fdecea' }}">
            <td>{{ $t['time'] ?? '' }}</td>
            <td>{{ $t['coin'] ?? '' }}</td>
            <td>{{ ($t['side'] ?? 0) == 1 ? 'LONG' : 'SHORT' }}</td>
            <td>{{ $t['status'] ?? '' }}</td>
            <td>{{ number_format($t['pnl_pct'] ?? 0, 2) }}%</td>
        </tr>
        @endforeach
    </table>
@endforeach

@if (empty($compare['periods']))
    <p>Нет данных сверки. Запустите compare_live.py, чтобы сгенерировать storage/app/futures_compare.json.</p>
@endif
@endsection
