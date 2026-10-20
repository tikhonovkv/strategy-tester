@extends('layouts.app')

@section('title', 'Futures Funding Bot — монитор')

@section('content')
@php
    $balance = $signals['usdt_free'] ?? null;
    $positions = $signals['open_positions'] ?? null;
    $periods = $compare['periods'] ?? [];
    $verified = 0; $matchedTotal = 0; $totalSig = 0; $fidelity = 0; $pnl30 = 0;
    $allTrades = []; $checked = 0; $off = 0;
    foreach ($periods as $p) {
        $totalSig += $p['total'] ?? 0;
        $matchedTotal += $p['matched'] ?? 0;
        if (($p['label'] ?? '') === 'Последние 30 дней') {
            $fidelity = $p['fidelity'] ?? 0;
            $pnl30 = $p['pnl_sum'] ?? 0;
            $allTrades = $p['trades'] ?? [];
        }
    }
    $verified = $matchedTotal;
    $chart = []; $cum = 0;
    foreach ($allTrades as $t) { $cum += $t['pnl_pct'] ?? 0; $chart[] = ['t' => $t['time'] ?? '', 'pnl' => round($cum, 2)]; }
    $lastRun = $allTrades ? end($allTrades)['time'] ?? '—' : '—';
    $W = 640; $H = 240; $n = count($chart);
    $lo = 0; $hi = 0;
    foreach ($chart as $c) { $lo = min($lo, $c['pnl']); $hi = max($hi, $c['pnl']); }
    $rng = max($hi - $lo, 0.01);
    $pts = '';
    foreach ($chart as $i => $c) {
        $x = $n > 1 ? $W * $i / ($n - 1) : 0;
        $y = $H - ($c['pnl'] - $lo) / $rng * $H;
        $pts .= number_format($x,1,'.','') . ',' . number_format($y,1,'.','') . ' ';
    }
    $zeroY = $H - (0 - $lo) / $rng * $H;
@endphp

<style>
    body { overflow: hidden; }
    .container { max-width: 100% !important; padding: 10px 16px 12px !important; }
    .monitor { display: flex; flex-direction: column; height: calc(100vh - 60px); gap: 10px; }
    .topbar { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 10px; }
    .clock { font-size: 19px; font-weight: 700; color: #10b981; font-variant-numeric: tabular-nums; }
    .live-dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: #10b981; animation: pulse 2s infinite; margin-right: 6px; }
    @keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.25; } }
    .kpis { display: flex; gap: 10px; flex-wrap: wrap; }
    .kpi { background: #0f1624; border: 1px solid #222d45; border-radius: 10px; padding: 8px 16px; min-width: 128px; }
    .kpi .l { font-size: 11px; color: #94a3b8; text-transform: uppercase; letter-spacing: .04em; }
    .kpi .v { font-size: 21px; font-weight: 700; color: #fff; line-height: 1.2; }
    .main { display: grid; grid-template-columns: 1.65fr 1fr; gap: 12px; flex: 1; min-height: 0; }
    .panel { background: #131a2a; border: 1px solid #222d45; border-radius: 10px; padding: 10px 12px; overflow: auto; display: flex; flex-direction: column; }
    .panel h2 { font-size: 13px; color: #94a3b8; text-transform: uppercase; letter-spacing: .05em; margin-bottom: 8px; }
    table { border-collapse: collapse; width: 100%; font-size: 12.5px; }
    td, th { padding: 4px 7px; border-bottom: 1px solid #222d45; text-align: left; white-space: nowrap; }
    th { color: #94a3b8; font-size: 10.5px; text-transform: uppercase; letter-spacing: .03em; position: sticky; top: 0; background: #131a2a; z-index: 1; }
    tr.matched td { background: rgba(16,185,129,0.10); }
    tr.missed td { background: rgba(239,68,68,0.10); }
    tr.warn td { background: rgba(245,158,11,0.13); }
    tr.bad td { background: rgba(239,68,68,0.22); }
    .ok { color: #10b981; font-weight: 700; } .bad { color: #ef4444; font-weight: 700; } .warn { color: #f59e0b; font-weight: 700; }
    .muted { color: #94a3b8; }
    .chart-wrap { flex: 1; min-height: 180px; }
</style>

<div class="monitor">
  <div class="topbar">
    <h1 style="font-size:17px; margin:0">Futures Funding Bot · сверка модель → счёт</h1>
    <div style="display:flex; align-items:center; gap:18px">
      <span class="muted">Сервер: <span id="clock" class="clock">{{ now()->format('Y-m-d H:i:s') }}</span></span>
      <span class="muted">Последний прогон бота: <b style="color:#e2e8f0">{{ $lastRun }}</b></span>
      <span><span class="live-dot"></span>авто-обновление 60с</span>
    </div>
  </div>

  <div class="kpis">
    <div class="kpi"><div class="l">Баланс USDT</div><div class="v">{{ $balance !== null ? number_format($balance, 2) : '—' }}</div></div>
    <div class="kpi"><div class="l">Открыто позиций</div><div class="v">{{ $positions ?? '—' }}</div></div>
    <div class="kpi"><div class="l">Проверено (совпало)</div><div class="v ok">{{ $verified }} <span class="muted" style="font-size:13px">/ {{ $totalSig }}</span></div></div>
    <div class="kpi"><div class="l">Точность следования</div><div class="v {{ $fidelity >= 90 ? 'ok' : ($fidelity >= 50 ? 'warn' : 'bad') }}">{{ $fidelity }}%</div></div>
    <div class="kpi"><div class="l">PnL 30д</div><div class="v {{ $pnl30 >= 0 ? 'ok' : 'bad' }}">{{ number_format($pnl30, 2) }}%</div></div>
  </div>

  <div class="main">
    <div class="panel">
      <h2>Сверка сделок (30 дней)</h2>
      @if (empty($allTrades))
        <p class="muted">Нет данных. Бот сгенерирует futures_compare.json в следующем цикле.</p>
      @else
        <table>
          <tr><th>Вход</th><th>Выход</th><th>Монета</th><th>Сторона</th><th>Цена вх.</th><th>Цена вых.</th><th>Объём</th><th>Проверка</th><th>Расхождение</th><th>PnL</th></tr>
          @foreach ($allTrades as $t)
          @php
            $q = $t['quality'] ?? 'OK';
            $rowCls = strtolower($t['status'] ?? '');
            if (($t['status'] ?? '') === 'MATCHED' && $q !== 'OK') { $rowCls .= $q === 'PRICE_QTY' ? ' bad' : ' warn'; }
            $icon = '✗'; $iconCls = 'bad';
            if (($t['status'] ?? '') === 'MATCHED') { $icon = $q === 'OK' ? '✓' : '⚠'; $iconCls = $q === 'OK' ? 'ok' : 'warn'; }
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
            <td class="{{ $iconCls }}">{{ $icon }}</td>
            <td class="{{ $disc === 'OK' ? 'ok' : ($disc ? 'warn' : '') }}">{{ $disc ?: '—' }}</td>
            <td class="{{ ($t['pnl_pct'] ?? 0) >= 0 ? 'ok' : 'bad' }}">{{ number_format($t['pnl_pct'] ?? 0, 2) }}%</td>
          </tr>
          @endforeach
        </table>
      @endif
    </div>

    <div class="panel">
      <h2>PnL (накопительно, 30 дней)</h2>
      <div class="chart-wrap">
        @if ($n > 1)
        <svg viewBox="0 0 640 240" preserveAspectRatio="none" style="width:100%;height:100%">
          <line x1="0" y1="{{ number_format($zeroY,1,'.','') }}" x2="640" y2="{{ number_format($zeroY,1,'.','') }}" stroke="#222d45" stroke-width="1"/>
          <polyline points="{{ trim($pts) }}" fill="none" stroke="#10b981" stroke-width="2"/>
        </svg>
        <div style="display:flex; justify-content:space-between; font-size:11px; color:#94a3b8; margin-top:4px">
          <span>{{ $chart[0]['t'] ?? '' }}</span>
          <span>{{ end($chart)['t'] ?? '' }}</span>
        </div>
        @else
        <p class="muted">Недостаточно сделок для графика.</p>
        @endif
      </div>
    </div>
  </div>
</div>

<script>
  var serverTs = {{ now()->timestamp }};
  var offset = Math.floor(Date.now() / 1000) - serverTs;
  function tick() {
    var d = new Date((Math.floor(Date.now() / 1000) - offset) * 1000);
    var pad = function(x){ return (x < 10 ? '0' : '') + x; };
    document.getElementById('clock').textContent =
      d.getFullYear() + '-' + pad(d.getMonth()+1) + '-' + pad(d.getDate()) + ' ' +
      pad(d.getHours()) + ':' + pad(d.getMinutes()) + ':' + pad(d.getSeconds());
  }
  setInterval(tick, 1000);
  tick();
  setTimeout(function(){ location.reload(); }, 60000);
</script>
@endsection
