@extends('layouts.app')

@section('content')
<h1>Фьючерсы (funding модель, 9-обучение / 4-торговля)</h1>

<h2>Живые сигналы модели</h2>
@if (empty($signals['signals'] ?? []))
    <p>Нет свежих сигналов (файл storage/app/futures_signals.json пуст или отсутствует). Запустите live_bot.py.</p>
@else
    <table>
        <tr><th>Монета</th><th>Сторона</th><th>pred_net</th><th>Время</th></tr>
        @foreach ($signals['signals'] as $s)
        <tr>
            <td>{{ $s['symbol'] ?? '' }}</td>
            <td>{{ ($s['side'] ?? 0) == 1 ? 'LONG' : 'SHORT' }}</td>
            <td>{{ number_format(($s['pred_net'] ?? 0) * 100, 2) }}%</td>
            <td>{{ $s['ts'] ?? '' }}</td>
        </tr>
        @endforeach
    </table>
    <p>Баланс: {{ $signals['usdt_free'] ?? '' }} USDT · Открыто позиций: {{ $signals['open_positions'] ?? '' }}</p>
@endif

<h2>Сверка бэктест vs live</h2>
<p><a href="{{ route('futures.compare') }}">Открыть полную сверку (7д / 30д)</a></p>
@endsection
