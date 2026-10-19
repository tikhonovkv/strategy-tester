@extends('layouts.app')

@section('title', 'Futures funding бот — сверка модель → счёт')

@section('content')
<style>
    .kpi{display:flex;gap:14px;flex-wrap:wrap;margin:14px 0}
    .kpi .b{background:#0f1624;border:1px solid #222d45;border-radius:10px;padding:12px 16px;min-width:150px}
    .kpi .b .l{font-size:11px;color:#94a3b8;text-transform:uppercase;letter-spacing:.05em}
    .kpi .v{font-size:24px;font-weight:700;color:#fff;margin-top:4px}
    .ok{color:#10b981}.bad{color:#ef4444}.warn{color:#f59e0b}
    .small{font-size:12px;color:#94a3b8}
    h3{font-size:15px;color:#fff;margin:18px 0 6px}
    tr.matched td{background:rgba(16,185,129,0.12)}
    tr.missed td{background:rgba(239,68,68,0.12)}
    table{margin-top:8px}
</style>

<h1>Фьючерсный funding-бот · сверка «модель → счёт»</h1>
<p class="sub">Сквозная прозрачность: обучение → бэктест → живой бот → сверка сделок.</p>

<div class="card">
  <h2>1 · Модель (обучение)</h2>
  <table>
    <tr><th>Обучение</th><td>{{ $config['train'] }}</td></tr>
    <tr><th>Торговля</th><td>{{ $config['trade'] }}</td></tr>
    <tr><th>Окно / порог / плечо</th><td>{{ $config['window'] }} · {{ $config['threshold'] }} · {{ $config['leverage'] }}</td></tr>
    <tr><th>Ставка</th><td>{{ $config['stake'] }}</td></tr>
    <tr><th>Признаки</th><td>{{ $config['features'] }}</td></tr>
    <tr><th>Переобучение</th><td>{{ $config['retrain'] }}</td></tr>
  </table>
</div>

<div class="card">
  <h2>2 · Бэктест (проверка)</h2>
  <p><b style="color:#fff">{{ $config['backtest']['trades'] }}</b> сделок за {{ $config['backtest']['years'] }} лет.</p>
  <div class="kpi">
    <div class="b"><div class="l">плечо 2x</div><div class="v">{{ $config['backtest']['lev2'] }}</div></div>
    <div class="b"><div class="l">плечо 3x</div><div class="v">{{ $config['backtest']['lev3'] }}</div></div>
    <div class="b"><div class="l">плечо 4x</div><div class="v">{{ $config['backtest']['lev4'] }}</div></div>
  </div>
  <p class="small">Полный реинвест, без проскальзывания. Реалистично (фикс. размер + slippage 0.3%) на 3x: $2000 → ~$10 900.</p>
</div>

<div class="card">
  <h2>3 · Живой бот (сейчас)</h2>
  @if (empty($signals))
    <p class="small">Нет данных бота (futures_signals.json не найден). Бот ещё не прогнал цикл.</p>
  @else
    <div class="kpi">
      <div class="b"><div class="l">Баланс USDT</div><div class="v">{{ $signals['usdt_free'] ?? '—' }}</div></div>
      <div class="b"><div class="l">Открыто позиций</div><div class="v">{{ $signals['open_positions'] ?? '—' }}</div></div>
    </div>
    @if (!empty($signals['signals']))
      <h3>Сигналы модели</h3>
      <table>
        <tr><th>Монета</th><th>Сторона</th><th>pred_net</th><th>Время</th></tr>
        @foreach ($signals['signals'] as $s)
        <tr><td>{{ $s['symbol'] }}</td><td>{{ ($s['side'] ?? 0) == 1 ? 'LONG' : 'SHORT' }}</td><td>{{ number_format(($s['pred_net'] ?? 0) * 100, 2) }}%</td><td>{{ gmdate('Y-m-d H:i', ($s['ts'] ?? 0) / 1000) }}</td></tr>
        @endforeach
      </table>
    @else
      <p class="small">На текущем funding-тике сигналов выше порога нет.</p>
    @endif
  @endif
</div>

<div class="card">
  <h2>4 · Сверка: что сделал бот vs модель</h2>
  @if (empty($compare['periods']))
    <p class="small">Нет данных сверки. Бот сгенерирует futures_compare.json в следующем цикле.</p>
  @else
    @foreach ($compare['periods'] as $p)
      <h3>{{ $p['label'] }}</h3>
      <div class="kpi">
        <div class="b"><div class="l">Сигналов модели</div><div class="v">{{ $p['total'] }}</div></div>
        <div class="b"><div class="l">Совпало</div><div class="v ok">{{ $p['matched'] }}</div></div>
        <div class="b"><div class="l">Пропущено</div><div class="v warn">{{ $p['missed'] }}</div></div>
        <div class="b"><div class="l">Лишних (бот)</div><div class="v bad">{{ $p['extras'] }}</div></div>
        <div class="b"><div class="l">Точность следования</div><div class="v {{ ($p['fidelity'] ?? 0) >= 90 ? 'ok' : (($p['fidelity'] ?? 0) >= 50 ? 'warn' : 'bad') }}">{{ $p['fidelity'] }}%</div></div>
        <div class="b"><div class="l">PnL модели</div><div class="v">{{ number_format($p['pnl_sum'] ?? 0, 2) }}%</div></div>
      </div>
      <table>
        <tr><th>Время</th><th>Монета</th><th>Сторона</th><th>Статус</th><th>PnL</th></tr>
        @foreach ($p['trades'] as $t)
        <tr class="{{ strtolower($t['status'] ?? '') }}">
          <td>{{ $t['time'] }}</td><td>{{ $t['coin'] }}</td>
          <td>{{ ($t['side'] ?? 0) == 1 ? 'LONG' : 'SHORT' }}</td>
          <td>{{ $t['status'] }}</td><td>{{ number_format($t['pnl_pct'] ?? 0, 2) }}%</td>
        </tr>
        @endforeach
      </table>
    @endforeach
  @endif
</div>
@endsection
