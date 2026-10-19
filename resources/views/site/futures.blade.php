@extends('layouts.app')

@section('content')
<style>
.card{border:1px solid #e3e3e3;border-radius:10px;padding:16px;margin:14px 0;background:#fff}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:16px}
.kpi{display:flex;gap:16px;flex-wrap:wrap}
.kpi .b{border:1px solid #e3e3e3;border-radius:10px;padding:12px 18px;min-width:150px}
.kpi .v{font-size:24px;font-weight:700}
.ok{color:#1a7f37}.bad{color:#cf222e}.warn{color:#9a6700}
table{border-collapse:collapse;width:100%;font-size:13px}
td,th{padding:6px 8px;border-bottom:1px solid #eee;text-align:left}
tr.matched td{background:#eafaf0}tr.missed td{background:#fdecea}
.small{font-size:12px;color:#666}
</style>

<h1>Фьючерсный funding-бот · сверка «модель → счёт»</h1>

<div class="card">
  <h2>1 · Модель (обучение)</h2>
  <table>
    <tr><td>Обучение</td><td>{{ $config['train'] }}</td></tr>
    <tr><td>Торговля</td><td>{{ $config['trade'] }}</td></tr>
    <tr><td>Окно / порог / плечо</td><td>{{ $config['window'] }} · {{ $config['threshold'] }} · {{ $config['leverage'] }}</td></tr>
    <tr><td>Ставка</td><td>{{ $config['stake'] }}</td></tr>
    <tr><td>Признаки</td><td>{{ $config['features'] }}</td></tr>
    <tr><td>Переобучение</td><td>{{ $config['retrain'] }}</td></tr>
  </table>
</div>

<div class="card">
  <h2>2 · Бэктест (проверка)</h2>
  <p><b>{{ $config['backtest']['trades'] }}</b> сделок за {{ $config['backtest']['years'] }} лет.</p>
  <div class="kpi">
    <div class="b"><div>плечо 2x</div><div class="v">{{ $config['backtest']['lev2'] }}</div></div>
    <div class="b"><div>плечо 3x</div><div class="v">{{ $config['backtest']['lev3'] }}</div></div>
    <div class="b"><div>плечо 4x</div><div class="v">{{ $config['backtest']['lev4'] }}</div></div>
  </div>
  <p class="small">Полный реинвест, без проскальзывания. Реалистично (фикс. размер + slippage 0.3%) на 3x: $2000 → ~$10 900.</p>
</div>

<div class="card">
  <h2>3 · Живой бот (сейчас)</h2>
  @if (empty($signals))
    <p class="small">Нет данных бота (futures_signals.json не найден в {{ env('FUTURES_DATA_DIR', 'storage/app/futures') }}).</p>
  @else
    <div class="kpi">
      <div class="b"><div>Баланс USDT</div><div class="v">{{ $signals['usdt_free'] ?? '—' }}</div></div>
      <div class="b"><div>Открыто позиций</div><div class="v">{{ $signals['open_positions'] ?? '—' }}</div></div>
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
    <p class="small">Нет данных сверки. Запустите compare_live.py (сгенерирует futures_compare.json).</p>
  @else
    @foreach ($compare['periods'] as $p)
      <h3>{{ $p['label'] }}</h3>
      <div class="kpi">
        <div class="b"><div>Сигналов модели</div><div class="v">{{ $p['total'] }}</div></div>
        <div class="b"><div>Совпало</div><div class="v ok">{{ $p['matched'] }}</div></div>
        <div class="b"><div>Пропущено</div><div class="v warn">{{ $p['missed'] }}</div></div>
        <div class="b"><div>Лишних (бот)</div><div class="v bad">{{ $p['extras'] }}</div></div>
        <div class="b"><div>Точность следования</div><div class="v {{ ($p['fidelity'] ?? 0) >= 90 ? 'ok' : (($p['fidelity'] ?? 0) >= 50 ? 'warn' : 'bad') }}">{{ $p['fidelity'] }}%</div></div>
        <div class="b"><div>PnL модели</div><div class="v">{{ number_format($p['pnl_sum'] ?? 0, 2) }}%</div></div>
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
