@extends('layouts.app')

@section('title', 'Futures Funding Bot — монитор')

@section('content')
@php
    $balance = $signals['usdt_free'] ?? null;
    $positions = $signals['open_positions'] ?? null;
    $periods = $compare['periods'] ?? [];
    $lastRun = $compare['generated_at'] ?? '—';
    $periodKeys = ['Месяц', 'Квартал', 'Год'];
    $periodMap = [];
    foreach ($periods as $p) { $periodMap[$p['label'] ?? ''] = $p; }
    $active = $periodMap['Месяц'] ?? ($periods[0] ?? []);
    $verified = $active['verified'] ?? 0; $totalSig = $active['total'] ?? 0;
    $fidelity = $active['fidelity'] ?? 0; $pnlPeriod = $active['return_pct'] ?? 0;
    // chart data per period
    $chartData = [];
    foreach ($periods as $p) {
        $labels = []; $pnls = []; $prices = []; $cum = 1.0;
        foreach (array_reverse($p['trades'] ?? []) as $t) {
            $labels[] = $t['time'];
            $cum *= 1 + ($t['pnl_pct'] ?? 0) / 100;
            $pnls[] = round(($cum - 1) * 100, 2);
            $prices[] = $t['entry_price'] ?? null;
        }
        $chartData[$p['label'] ?? ''] = ['labels' => $labels, 'pnl' => $pnls, 'prices' => $prices];
    }
    $tradePaths = [];
    foreach ($periods as $p) {
        foreach ($p['trades'] ?? [] as $t) {
            $tid = md5(($t['time'] ?? '') . ($t['coin'] ?? '') . ($t['side'] ?? '') . ($t['entry_price'] ?? ''));
            $tradePaths[$tid] = $t['path'] ?? null;
        }
    }
@endphp

<style>
    body { overflow: hidden; }
    .container { max-width: 100% !important; padding: 10px 16px 12px !important; }
    .monitor { display: flex; flex-direction: column; height: calc(100vh - 60px); gap: 10px; }
    .topbar { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 10px; }
    .clock { font-size: 18px; font-weight: 700; color: #10b981; font-variant-numeric: tabular-nums; }
    .live-dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: #10b981; animation: pulse 2s infinite; margin-right: 6px; }
    @keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.25; } }
    .tabs { display: flex; gap: 6px; }
    .tab { padding: 6px 14px; border-radius: 7px; border: 1px solid #222d45; background: #0f1624; color: #94a3b8; cursor: pointer; font-size: 13px; font-weight: 600; }
    .tab.active { background: #3b82f6; color: #fff; border-color: #3b82f6; }
    .kpis { display: flex; gap: 10px; flex-wrap: wrap; }
    .kpi { background: #0f1624; border: 1px solid #222d45; border-radius: 10px; padding: 8px 16px; min-width: 128px; }
    .kpi .l { font-size: 11px; color: #94a3b8; text-transform: uppercase; letter-spacing: .04em; }
    .kpi .v { font-size: 21px; font-weight: 700; color: #fff; line-height: 1.2; }
    .main { display: grid; grid-template-columns: 1.6fr 1fr; gap: 12px; flex: 1; min-height: 0; }
    .panel { background: #131a2a; border: 1px solid #222d45; border-radius: 10px; padding: 10px 12px; overflow: auto; display: flex; flex-direction: column; }
    .panel h2 { font-size: 13px; color: #94a3b8; text-transform: uppercase; letter-spacing: .05em; margin-bottom: 8px; display: flex; justify-content: space-between; align-items: center; }
    table { border-collapse: collapse; width: 100%; font-size: 12.5px; }
    td, th { padding: 4px 7px; border-bottom: 1px solid #222d45; text-align: left; white-space: nowrap; }
    th { color: #94a3b8; font-size: 10.5px; text-transform: uppercase; letter-spacing: .03em; position: sticky; top: 0; background: #131a2a; z-index: 1; }
    tr.trade-row { cursor: pointer; }
    tr.matched td { background: rgba(16,185,129,0.10); }
    tr.missed td { background: rgba(239,68,68,0.10); }
    tr.warn td { background: rgba(245,158,11,0.13); }
    tr.bad td { background: rgba(239,68,68,0.22); }
    tr.new td { box-shadow: inset 3px 0 0 #3b82f6; }
    .ok { color: #10b981; font-weight: 700; } .bad { color: #ef4444; font-weight: 700; } .warn { color: #f59e0b; font-weight: 700; }
    .muted { color: #94a3b8; }
    .chart-wrap { flex: 1; min-height: 200px; position: relative; }
    .chart-tools { display: flex; gap: 6px; }
    .tbtn { padding: 4px 10px; border-radius: 6px; border: 1px solid #222d45; background: #0f1624; color: #94a3b8; cursor: pointer; font-size: 12px; }
    .tbtn.active { background: #3b82f6; color: #fff; }
    .close-x { cursor: pointer; color: #ef4444; font-weight: 700; border: 1px solid #ef4444; border-radius: 6px; padding: 2px 8px; font-size: 12px; }
</style>

<div class="monitor">
  <div class="topbar">
    <h1 style="font-size:17px; margin:0">Futures Funding Bot · сверка модель → счёт</h1>
    <div class="tabs" id="periodTabs">
      @foreach ($periodKeys as $k)
      <div class="tab {{ $k === 'Месяц' ? 'active' : '' }}" data-period="{{ $k }}">{{ $k }}</div>
      @endforeach
    </div>
    <div style="display:flex; align-items:center; gap:16px">
      <span class="muted">Сервер: <span id="clock" class="clock">{{ now()->format('Y-m-d H:i:s') }}</span></span>
      <span class="muted">Прогон бота: <b style="color:#e2e8f0">{{ $lastRun }}</b></span>
      <span><span class="live-dot"></span>авто-обновление 60с</span>
    </div>
  </div>

  <div class="kpis">
    <div class="kpi"><div class="l">Баланс USDT</div><div class="v">{{ $balance !== null ? number_format($balance, 2) : '—' }}</div></div>
    <div class="kpi"><div class="l">Открыто позиций</div><div class="v">{{ $positions ?? '—' }}</div></div>
    <div class="kpi"><div class="l">Совпало точно</div><div class="v ok"><span id="kpiVerified">{{ $verified }}</span> <span class="muted" style="font-size:13px">/ <span id="kpiTotal">{{ $totalSig }}</span></span></div></div>
    <div class="kpi"><div class="l">Точность</div><div class="v" id="kpiFid">{{ $fidelity }}%</div></div>
    <div class="kpi"><div class="l">PnL (период)</div><div class="v" id="kpiPnl">{{ number_format($pnlPeriod, 2) }}%</div></div>
  </div>

  <div class="main">
    <div class="panel" id="tablePanel">
      <h2>Сверка сделок <span class="muted" id="periodLabel" style="text-transform:none;letter-spacing:0">— {{ $active['label'] ?? '' }}</span>
        <span><button class="tbtn" id="markAllBtn">Отметить всё просмотренным</button></span>
      </h2>
      @foreach ($periods as $p)
      <div class="period-table" data-period="{{ $p['label'] }}" style="{{ $p['label'] === 'Месяц' ? '' : 'display:none' }}">
        @if (empty($p['trades']))
          <p class="muted">Нет данных.</p>
        @else
        <table>
          <tr><th>Вход</th><th>Выход</th><th>Монета</th><th>Сторона</th><th>Цена вх.</th><th>Цена вых.</th><th>Объём</th><th>Проверка</th><th>Расхождение</th><th>PnL</th></tr>
          @foreach ($p['trades'] as $t)
          @php
            $q = $t['quality'] ?? 'OK';
            $st = $t['status'] ?? '';
            $rowCls = strtolower($st);
            if ($st === 'MATCHED' && $q !== 'OK') { $rowCls .= $q === 'PRICE_QTY' ? ' bad' : ' warn'; }
            $icon = '✗'; $iconCls = 'bad';
            if ($st === 'MATCHED') { $icon = $q === 'OK' ? '✓' : '⚠'; $iconCls = $q === 'OK' ? 'ok' : 'warn'; }
            $disc = '';
            if ($st === 'MATCHED') {
                $parts = [];
                if (isset($t['price_diff_pct']) && $t['price_diff_pct'] > 0.5) { $parts[] = 'цена ' . $t['price_diff_pct'] . '%'; }
                if (isset($t['qty_diff_pct']) && $t['qty_diff_pct'] > 15) { $parts[] = 'объём ' . $t['qty_diff_pct'] . '%'; }
                $disc = $parts ? implode(' / ', $parts) : 'OK';
            }
            $tid = md5(($t['time'] ?? '') . ($t['coin'] ?? '') . ($t['side'] ?? '') . ($t['entry_price'] ?? ''));
            $tip = $disc && $disc !== 'OK' ? $disc : ($st === 'MISSED' ? 'не исполнено' : '');
          @endphp
          <tr class="trade-row {{ $rowCls }}" data-id="{{ $tid }}" data-coin="{{ $t['coin'] }}" data-side="{{ $t['side'] }}" data-entry="{{ $t['entry_price'] }}" data-exit="{{ $t['exit_price'] }}" data-time="{{ $t['time'] }}" data-exittime="{{ $t['exit_time'] }}" data-pnl="{{ $t['pnl_pct'] }}">
            <td>{{ $t['time'] }}</td><td>{{ $t['exit_time'] ?? '—' }}</td><td>{{ $t['coin'] }}</td>
            <td>{{ ($t['side'] ?? 0) == 1 ? 'LONG' : 'SHORT' }}</td>
            <td>{{ $t['entry_price'] ?? '—' }}</td><td>{{ $t['exit_price'] ?? '—' }}</td><td>{{ $t['qty'] ?? '—' }}</td>
            <td class="{{ $iconCls }}" title="{{ $tip }}">{{ $icon }}</td>
            <td class="{{ $disc === 'OK' ? 'ok' : ($disc ? 'warn' : '') }}">{{ $disc ?: '—' }}</td>
            <td class="{{ ($t['pnl_pct'] ?? 0) >= 0 ? 'ok' : 'bad' }}">{{ number_format($t['pnl_pct'] ?? 0, 2) }}%</td>
          </tr>
          @endforeach
        </table>
        @endif
      </div>
      @endforeach
    </div>

    <div class="panel">
      <h2><span id="chartTitle">PnL (накопительно)</span>
        <span class="chart-tools">
          <button class="tbtn active" id="btnPnl">PnL</button>
          <button class="tbtn" id="btnPrice">Цена</button>
          <button class="close-x" id="btnClose" style="display:none">✕ закрыть</button>
        </span>
      </h2>
      <div class="chart-wrap">
        <canvas id="mainChart"></canvas>
      </div>
    </div>
  </div>
</div>

<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<script>
var chartData = @json($chartData);
var tradePaths = @json($tradePaths);
var CHART = null;
var currentMode = 'pnl';
var currentPeriod = 'Месяц';

function buildDataset() {
  var d = chartData[currentPeriod] || { labels: [], pnl: [], prices: [] };
  var data = currentMode === 'pnl' ? d.pnl : d.prices;
  return {
    labels: d.labels,
    datasets: [{
      label: currentMode === 'pnl' ? 'PnL %' : 'Цена',
      data: data,
      borderColor: currentMode === 'pnl' ? '#10b981' : '#3b82f6',
      backgroundColor: 'rgba(16,185,129,0.08)',
      fill: currentMode === 'pnl',
      tension: 0.25,
      pointRadius: 0
    }]
  };
}

function renderMain() {
  var ctx = document.getElementById('mainChart');
  var cfg = buildDataset();
  if (CHART) { CHART.destroy(); }
  CHART = new Chart(ctx, {
    type: 'line',
    data: cfg,
    options: {
      responsive: true, maintainAspectRatio: false,
      animation: false,
      plugins: {
        legend: { display: false },
        tooltip: { callbacks: { label: function(c){ return (currentMode === 'pnl' ? 'PnL: ' : 'Цена: ') + c.parsed.y + (currentMode === 'pnl' ? '%' : ''); } } }
      },
      scales: {
        x: { ticks: { color: '#94a3b8', maxTicksLimit: 8, font: { size: 10 } }, grid: { color: '#222d45' } },
        y: { ticks: { color: '#94a3b8', font: { size: 10 }, callback: function(v){ return currentMode === 'pnl' ? v + '%' : v; } }, grid: { color: '#222d45' } }
      }
    }
  });
  document.getElementById('chartTitle').textContent = currentMode === 'pnl' ? 'PnL (накопительно) — ' + currentPeriod : 'Цена входа — ' + currentPeriod;
}

function renderTrade(tr) {
  var id = tr.getAttribute('data-id');
  var coin = tr.getAttribute('data-coin');
  var side = tr.getAttribute('data-side') === '1' ? 'LONG' : 'SHORT';
  var entry = parseFloat(tr.getAttribute('data-entry'));
  var exit = parseFloat(tr.getAttribute('data-exit'));
  var pnl = parseFloat(tr.getAttribute('data-pnl'));
  var t0 = tr.getAttribute('data-time');
  var t1 = tr.getAttribute('data-exittime');
  var path = tradePaths[id];
  var ctx = document.getElementById('mainChart');
  if (CHART) { CHART.destroy(); }
  if (path && path.length > 2) {
    var entryIdx = 2;
    var exitIdx = path.length - 3;
    var labels = path.map(function(_, i){ return i === entryIdx ? 'ВХОД' : (i === exitIdx ? 'ВЫХОД' : ''); });
    var radii = path.map(function(_, i){ return (i === entryIdx || i === exitIdx) ? 5 : 0; });
    var colors = path.map(function(_, i){ return i === entryIdx ? '#3b82f6' : (i === exitIdx ? (pnl >= 0 ? '#10b981' : '#ef4444') : '#94a3b8'); });
    CHART = new Chart(ctx, {
      type: 'line',
      data: {
        labels: labels,
        datasets: [{
          label: coin,
          data: path,
          borderColor: '#e2e8f0',
          backgroundColor: 'rgba(59,130,246,0.08)',
          fill: true,
          tension: 0.15,
          pointRadius: radii,
          pointBackgroundColor: colors,
          spanGaps: true
        }]
      },
      options: {
        responsive: true, maintainAspectRatio: false, animation: false,
        plugins: {
          legend: { display: false },
          title: { display: true, text: coin + ' ' + side + ' · вход ' + entry + ' → выход ' + exit + ' · PnL ' + pnl + '% (часовой график)', color: '#e2e8f0', font: { size: 12 } }
        },
        scales: {
          x: { ticks: { color: '#94a3b8', font: { size: 10 } }, grid: { color: '#222d45' } },
          y: { ticks: { color: '#94a3b8', font: { size: 10 } }, grid: { color: '#222d45' } }
        }
      }
    });
  } else {
    CHART = new Chart(ctx, {
      type: 'line',
      data: {
        labels: [t0, t1],
        datasets: [{
          label: coin + ' ' + side,
          data: [entry, exit],
          borderColor: pnl >= 0 ? '#10b981' : '#ef4444',
          pointBackgroundColor: ['#3b82f6', pnl >= 0 ? '#10b981' : '#ef4444'],
          pointRadius: 6,
          tension: 0
        }]
      },
      options: {
        responsive: true, maintainAspectRatio: false, animation: false,
        plugins: { legend: { display: false }, title: { display: true, text: coin + ' ' + side + ' · вход ' + entry + ' → выход ' + exit + ' · PnL ' + pnl + '%', color: '#e2e8f0', font: { size: 12 } } },
        scales: {
          x: { ticks: { color: '#94a3b8', font: { size: 10 } }, grid: { color: '#222d45' } },
          y: { ticks: { color: '#94a3b8', font: { size: 10 } }, grid: { color: '#222d45' } }
        }
      }
    });
  }
  document.getElementById('chartTitle').textContent = 'Сделка: ' + coin + ' ' + side;
  document.getElementById('btnClose').style.display = '';
  document.getElementById('btnPnl').classList.remove('active');
  document.getElementById('btnPrice').classList.remove('active');
}

// period tabs
document.querySelectorAll('#periodTabs .tab').forEach(function(tab) {
  tab.addEventListener('click', function() {
    currentPeriod = tab.getAttribute('data-period');
    document.querySelectorAll('#periodTabs .tab').forEach(function(t){ t.classList.remove('active'); });
    tab.classList.add('active');
    document.querySelectorAll('.period-table').forEach(function(p){
      p.style.display = p.getAttribute('data-period') === currentPeriod ? '' : 'none';
    });
    document.getElementById('periodLabel').textContent = '— ' + currentPeriod;
    var p = chartData[currentPeriod] ? null : null;
    // update KPIs from the period map (server-rendered; refresh on next cycle)
    renderMain();
  });
});

// PnL / price toggle
document.getElementById('btnPnl').addEventListener('click', function() {
  currentMode = 'pnl';
  document.getElementById('btnPnl').classList.add('active');
  document.getElementById('btnPrice').classList.remove('active');
  document.getElementById('btnClose').style.display = 'none';
  renderMain();
});
document.getElementById('btnPrice').addEventListener('click', function() {
  currentMode = 'price';
  document.getElementById('btnPrice').classList.add('active');
  document.getElementById('btnPnl').classList.remove('active');
  document.getElementById('btnClose').style.display = 'none';
  renderMain();
});
document.getElementById('btnClose').addEventListener('click', function() {
  document.getElementById('btnClose').style.display = 'none';
  renderMain();
});

// trade click -> trade chart
document.querySelectorAll('tr.trade-row').forEach(function(tr) {
  tr.addEventListener('click', function() {
    renderTrade(tr);
    markSeen(tr.getAttribute('data-id'));
  });
});

// localStorage: unviewed highlighting
var seen = [];
try { seen = JSON.parse(localStorage.getItem('seenTrades') || '[]'); } catch(e) { seen = []; }
document.querySelectorAll('tr.trade-row').forEach(function(tr) {
  var id = tr.getAttribute('data-id');
  if (seen.indexOf(id) === -1) { tr.classList.add('new'); }
});
function markSeen(id) {
  if (seen.indexOf(id) === -1) { seen.push(id); }
  localStorage.setItem('seenTrades', JSON.stringify(seen));
  document.querySelectorAll('tr.trade-row').forEach(function(tr) {
    if (tr.getAttribute('data-id') === id) { tr.classList.remove('new'); }
  });
}
document.getElementById('markAllBtn').addEventListener('click', function() {
  document.querySelectorAll('tr.trade-row').forEach(function(tr) {
    var id = tr.getAttribute('data-id');
    if (seen.indexOf(id) === -1) { seen.push(id); }
    tr.classList.remove('new');
  });
  localStorage.setItem('seenTrades', JSON.stringify(seen));
});

// server clock
var serverTs = {{ now()->timestamp }};
var offset = Math.floor(Date.now() / 1000) - serverTs;
function pad(x){ return (x < 10 ? '0' : '') + x; }
function tick() {
  var d = new Date((Math.floor(Date.now() / 1000) - offset) * 1000);
  document.getElementById('clock').textContent = d.getFullYear() + '-' + pad(d.getMonth()+1) + '-' + pad(d.getDate()) + ' ' + pad(d.getHours()) + ':' + pad(d.getMinutes()) + ':' + pad(d.getSeconds());
}
setInterval(tick, 1000); tick();

// auto refresh
setTimeout(function(){ location.reload(); }, 60000);

renderMain();
</script>
@endsection
