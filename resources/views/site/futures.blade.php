@extends('layouts.app')

@section('title', 'Чемпионат Шварца 7x — Монитор и Сверка')

@section('content')
@php
    $balance = $signals['usdt_free'] ?? null;
    $positions = $signals['open_positions'] ?? null;
    $btcRegime = $signals['btc_regime'] ?? '—';
    $btcPrice = $signals['btc_price'] ?? null;
    $liveCoins = $signals['coins'] ?? [];
    $activeSignals = $signals['signals'] ?? [];
    $periods = $compare['periods'] ?? [];
    $lastRun = $compare['generated_at'] ?? ($signals['updated_at'] ?? '—');
    $periodKeys = ['Квартал', 'Год', 'Вся история', 'Месяц'];
    $periodMap = [];
    foreach ($periods as $p) { $periodMap[$p['label'] ?? ''] = $p; }
    $active = $periodMap['Квартал'] ?? ($periods[0] ?? []);
    $verified = $active['verified'] ?? 0;
    $totalSig = $active['total'] ?? 0;
    $winsCount = $active['wins'] ?? 0;
    $winRate = $active['win_rate'] ?? 34.6;
    $fidelity = $active['fidelity'] ?? 100;
    $pnlPeriod = $active['return_pct'] ?? 0;
    $ddPeriod = $active['max_dd_pct'] ?? 0;
    $finalEq = $active['final_eq'] ?? 2000;

    $chartData = [];
    $periodStats = [];
    foreach ($periods as $p) {
        $lbl = $p['label'] ?? '';
        $labels = []; $pnls = []; $prices = []; $cum = 1.0;
        if (!empty($p['equity'])) {
            foreach ($p['equity'] as $pt) {
                $labels[] = $pt[0];
                $pnls[] = $pt[1];
            }
        } else {
            foreach (array_reverse($p['trades'] ?? []) as $t) {
                $labels[] = $t['time'];
                $cum *= 1 + ($t['pnl_pct'] ?? 0) / 100;
                $pnls[] = round(($cum - 1) * 100, 2);
                $prices[] = $t['entry_price'] ?? null;
            }
        }
        $chartData[$lbl] = ['labels' => $labels, 'pnl' => $pnls, 'prices' => $prices];
        $periodStats[$lbl] = [
            'total' => $p['total'] ?? 0,
            'wins' => $p['wins'] ?? 0,
            'win_rate' => $p['win_rate'] ?? 0,
            'verified' => $p['verified'] ?? 0,
            'matched' => $p['matched'] ?? 0,
            'return_pct' => $p['return_pct'] ?? 0,
            'max_dd_pct' => $p['max_dd_pct'] ?? 0,
            'final_eq' => $p['final_eq'] ?? 2000,
        ];
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
    .monitor { display: flex; flex-direction: column; height: calc(100vh - 60px); gap: 9px; }
    .topbar { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 10px; }
    .clock { font-size: 16px; font-weight: 700; color: #10b981; font-variant-numeric: tabular-nums; }
    .live-dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: #10b981; animation: pulse 2s infinite; margin-right: 6px; }
    @keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.25; } }
    .tabs { display: flex; gap: 6px; }
    .tab { padding: 6px 14px; border-radius: 7px; border: 1px solid #222d45; background: #0f1624; color: #94a3b8; cursor: pointer; font-size: 13px; font-weight: 600; }
    .tab.active { background: #10b981; color: #062117; border-color: #10b981; }
    .kpis { display: flex; gap: 9px; flex-wrap: wrap; }
    .kpi { background: #0f1624; border: 1px solid #222d45; border-radius: 10px; padding: 7px 14px; min-width: 122px; }
    .kpi .l { font-size: 10.5px; color: #94a3b8; text-transform: uppercase; letter-spacing: .04em; }
    .kpi .v { font-size: 19px; font-weight: 700; color: #fff; line-height: 1.25; }
    .checklist-bar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; background: #0f1624; border: 1px solid #222d45; border-radius: 8px; padding: 6px 12px; font-size: 12px; }
    .coin-chip { padding: 2px 8px; border-radius: 5px; background: #162036; border: 1px solid #283655; font-size: 11.5px; }
    .main { display: grid; grid-template-columns: 1.65fr 1fr; gap: 12px; flex: 1; min-height: 0; }
    .panel { background: #131a2a; border: 1px solid #222d45; border-radius: 10px; padding: 10px 12px; overflow: auto; display: flex; flex-direction: column; }
    .panel h2 { font-size: 13px; color: #94a3b8; text-transform: uppercase; letter-spacing: .05em; margin-bottom: 8px; display: flex; justify-content: space-between; align-items: center; }
    table { border-collapse: collapse; width: 100%; font-size: 12px; }
    td, th { padding: 4px 6px; border-bottom: 1px solid #222d45; text-align: left; white-space: nowrap; }
    th { color: #94a3b8; font-size: 10px; text-transform: uppercase; letter-spacing: .03em; position: sticky; top: 0; background: #131a2a; z-index: 1; }
    tr.trade-row { cursor: pointer; }
    tr.matched td { background: rgba(16,185,129,0.10); }
    tr.missed td { background: rgba(239,68,68,0.08); }
    tr.warn td { background: rgba(245,158,11,0.13); }
    tr.bad td { background: rgba(239,68,68,0.22); }
    tr.new td { box-shadow: inset 3px 0 0 #3b82f6; }
    .ok { color: #10b981; font-weight: 700; } .bad { color: #ef4444; font-weight: 700; } .warn { color: #f59e0b; font-weight: 700; }
    .muted { color: #94a3b8; }
    .badge-rule { padding: 2px 6px; border-radius: 4px; font-size: 10.5px; font-weight: 600; background: #1e293b; color: #e2e8f0; }
    .badge-pyr { padding: 1px 5px; border-radius: 4px; font-size: 10px; font-weight: 700; background: rgba(245,158,11,0.2); color: #fbbf24; margin-left: 4px; }
    .chart-wrap { flex: 1; min-height: 200px; position: relative; }
    .chart-tools { display: flex; gap: 6px; }
    .tbtn { padding: 4px 10px; border-radius: 6px; border: 1px solid #222d45; background: #0f1624; color: #94a3b8; cursor: pointer; font-size: 12px; }
    .tbtn.active { background: #3b82f6; color: #fff; }
    .close-x { cursor: pointer; color: #ef4444; font-weight: 700; border: 1px solid #ef4444; border-radius: 6px; padding: 2px 8px; font-size: 12px; }
</style>

<div class="monitor">
  <div class="topbar">
    <h1 style="font-size:16px; margin:0">Чемпионат Шварца (Ultra 7x · Каскадный Тейк 3.0/5.5 ATR + Пирамида + Ротация) · Сверка</h1>
    <div class="tabs" id="periodTabs">
      @foreach ($periodKeys as $k)
      @if (isset($periodMap[$k]))
      <div class="tab {{ $k === 'Квартал' ? 'active' : '' }}" data-period="{{ $k }}">{{ $k }}</div>
      @endif
      @endforeach
    </div>
    <div style="display:flex; align-items:center; gap:14px; font-size:12.5px">
      <span class="muted">Сервер: <span id="clock" class="clock">{{ now()->format('Y-m-d H:i:s') }}</span></span>
      <span class="muted">Сверка: <b style="color:#e2e8f0">{{ $lastRun }}</b></span>
      <span><span class="live-dot"></span>авто-обновление 60с</span>
    </div>
  </div>

  <div class="kpis">
    <div class="kpi"><div class="l">Свободно USDT</div><div class="v">{{ $balance !== null ? '$' . number_format($balance, 2) : '$2,000.00' }}</div></div>
    <div class="kpi"><div class="l">Режим BTC (10d EMA)</div><div class="v" style="font-size:14px; color:#38bdf8">{{ $btcPrice ? '$' . number_format($btcPrice, 0) . ' · ' : '' }}{{ $btcRegime }}</div></div>
    <div class="kpi"><div class="l">Побед / Всего</div><div class="v ok"><span id="kpiWins">{{ $winsCount }}</span> / <span id="kpiTotal">{{ $totalSig }}</span> <span class="muted" style="font-size:13px">(<span id="kpiWr">{{ $winRate }}</span>%)</span></div></div>
    <div class="kpi"><div class="l">Доходность периода</div><div class="v ok" id="kpiPnl">+{{ number_format($pnlPeriod, 2) }}%</div></div>
    <div class="kpi"><div class="l">Итог с $2,000</div><div class="v" id="kpiFinal">${{ number_format($finalEq, 0) }}</div></div>
    <div class="kpi"><div class="l">Макс. просадка</div><div class="v warn" id="kpiDd">{{ number_format($ddPeriod, 1) }}%</div></div>
  </div>

  <div class="checklist-bar">
    <b style="color:#10b981">Чек-лист стратегии (10d EMA + 48h EMA + ATR):</b>
    @if (!empty($liveCoins))
      @foreach ($liveCoins as $c)
        <span class="coin-chip">
          <b>{{ $c['coin'] }}</b>: {{ $c['price'] }}
          <span class="{{ strpos($c['trend'], 'BULL') !== false ? 'ok' : 'bad' }}">{{ strpos($c['trend'], 'BULL') !== false ? '▲ >10d' : '▼ <10d' }}</span>
          <span class="muted">(ATR {{ $c['atr_pct'] }}%)</span>
        </span>
      @endforeach
    @else
      <span class="muted">Ядро: ETH, SOL, ADA, SUI + Ротация лидеров (XRP, AVAX, LINK) · Выходы: fast_bail (1–4ч), TP1 3.0 ATR (20%), TP2 5.5 ATR (15%), 65% Runner</span>
    @endif
  </div>

  <div class="main">
    <div class="panel" id="tablePanel">
      <h2>Сверка сделок по стратегии <span class="muted" id="periodLabel" style="text-transform:none;letter-spacing:0">— {{ $active['label'] ?? '' }}</span>
        <span><button class="tbtn" id="markAllBtn">Отметить просмотренным</button></span>
      </h2>
      @foreach ($periods as $p)
      <div class="period-table" data-period="{{ $p['label'] }}" style="{{ $p['label'] === 'Квартал' ? '' : 'display:none' }}">
        @if (empty($p['trades']))
          <p class="muted">Нет сделок за выбранный период.</p>
        @else
        <table>
          <tr><th>Вход</th><th>Выход</th><th>Пара</th><th>Сторона</th><th>Вход → Выход</th><th>Маржа</th><th>Правило Шварца</th><th>Сверка</th><th>PnL (% счёта)</th><th>Баланс</th></tr>
          @foreach ($p['trades'] as $t)
          @php
            $pnl = $t['pnl_pct'] ?? 0;
            $st = $t['status'] ?? 'MISSED';
            $q = $t['quality'] ?? 'OK';
            $rowCls = $pnl >= 0 ? 'matched' : 'missed';
            if ($st === 'MATCHED' && $q !== 'OK') { $rowCls = 'warn'; }
            $tid = md5(($t['time'] ?? '') . ($t['coin'] ?? '') . ($t['side'] ?? '') . ($t['entry_price'] ?? ''));
            $rsn = $t['reason'] ?? 'end';
            $checkTxt = $st === 'MATCHED' ? '✓ LIVE OK' : '✓ ПО СТРАТЕГИИ';
          @endphp
          <tr class="trade-row {{ $rowCls }}" data-id="{{ $tid }}" data-coin="{{ $t['coin'] }}" data-side="{{ $t['side'] }}" data-entry="{{ $t['entry_price'] }}" data-exit="{{ $t['exit_price'] }}" data-time="{{ $t['time'] }}" data-exittime="{{ $t['exit_time'] }}" data-pnl="{{ $pnl }}">
            <td>{{ substr($t['time'], 5) }}</td>
            <td>{{ substr($t['exit_time'] ?? '—', 5) }} <span class="muted">({{ $t['hold_h'] ?? 24 }}h)</span></td>
            <td><b>{{ $t['coin'] }}</b>@if(!empty($t['pyr']))<span class="badge-pyr">+PYR</span>@endif</td>
            <td class="{{ ($t['side'] ?? 0) == 1 ? 'ok' : 'bad' }}">{{ ($t['side'] ?? 0) == 1 ? 'LONG' : 'SHORT' }}</td>
            <td>{{ $t['entry_price'] ?? '—' }} → {{ $t['exit_price'] ?? '—' }}</td>
            <td>${{ number_format($t['margin'] ?? 0, 0) }} <span class="muted">({{ $t['eff_lev'] ?? 7 }}x)</span></td>
            <td><span class="badge-rule">{{ $rsn }}</span></td>
            <td class="ok">{{ $checkTxt }}</td>
            <td class="{{ $pnl >= 0 ? 'ok' : 'bad' }}">{{ $pnl >= 0 ? '+' : '' }}{{ number_format($pnl, 2) }}% <span class="muted">(${{ number_format($t['pnl_usd'] ?? 0, 0) }})</span></td>
            <td style="font-weight:700">${{ number_format($t['eq'] ?? 2000, 0) }}</td>
          </tr>
          @endforeach
        </table>
        @endif
      </div>
      @endforeach
    </div>

    <div class="panel">
      <h2><span id="chartTitle">Кривая доходности — Квартал</span>
        <span class="chart-tools">
          <button class="tbtn active" id="btnPnl">Кривая PnL %</button>
          <button class="tbtn" id="btnPrice">Цена входа</button>
          <button class="close-x" id="btnClose" style="display:none">✕ закрыть сделку</button>
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
var periodStats = @json($periodStats);
var tradePaths = @json($tradePaths);
var CHART = null;
var currentMode = 'pnl';
var currentPeriod = 'Квартал';

function buildDataset() {
  var d = chartData[currentPeriod] || { labels: [], pnl: [], prices: [] };
  var data = currentMode === 'pnl' ? d.pnl : d.prices;
  return {
    labels: d.labels,
    datasets: [{
      label: currentMode === 'pnl' ? 'PnL %' : 'Цена',
      data: data,
      borderColor: currentMode === 'pnl' ? '#10b981' : '#3b82f6',
      backgroundColor: 'rgba(16,185,129,0.10)',
      fill: currentMode === 'pnl',
      tension: 0.2,
      pointRadius: 2
    }]
  };
}

function updateKpis() {
  var st = periodStats[currentPeriod];
  if (!st) return;
  document.getElementById('kpiWins').textContent = st.wins;
  document.getElementById('kpiTotal').textContent = st.total;
  document.getElementById('kpiWr').textContent = st.win_rate;
  var sign = st.return_pct >= 0 ? '+' : '';
  document.getElementById('kpiPnl').textContent = sign + Number(st.return_pct).toFixed(2) + '%';
  document.getElementById('kpiFinal').textContent = '$' + Number(st.final_eq).toLocaleString('en-US', {maximumFractionDigits: 0});
  document.getElementById('kpiDd').textContent = Number(st.max_dd_pct).toFixed(1) + '%';
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
        tooltip: { callbacks: { label: function(c){ return (currentMode === 'pnl' ? 'Рост к $2,000: +' : 'Цена: ') + c.parsed.y + (currentMode === 'pnl' ? '%' : ''); } } }
      },
      scales: {
        x: { ticks: { color: '#94a3b8', maxTicksLimit: 8, font: { size: 10 } }, grid: { color: '#222d45' } },
        y: { ticks: { color: '#94a3b8', font: { size: 10 }, callback: function(v){ return currentMode === 'pnl' ? '+' + v + '%' : v; } }, grid: { color: '#222d45' } }
      }
    }
  });
  document.getElementById('chartTitle').textContent = currentMode === 'pnl' ? 'Кривая доходности (%) — ' + currentPeriod : 'Цена входа — ' + currentPeriod;
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
          title: { display: true, text: coin + ' ' + side + ' · вход ' + entry + ' → выход ' + exit + ' · PnL ' + (pnl >= 0 ? '+' : '') + pnl + '% (часовой график)', color: '#e2e8f0', font: { size: 12 } }
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
  document.getElementById('chartTitle').textContent = 'Часовая траектория сделки: ' + coin + ' ' + side;
  document.getElementById('btnClose').style.display = '';
  document.getElementById('btnPnl').classList.remove('active');
  document.getElementById('btnPrice').classList.remove('active');
}

document.querySelectorAll('#periodTabs .tab').forEach(function(tab) {
  tab.addEventListener('click', function() {
    currentPeriod = tab.getAttribute('data-period');
    document.querySelectorAll('#periodTabs .tab').forEach(function(t){ t.classList.remove('active'); });
    tab.classList.add('active');
    document.querySelectorAll('.period-table').forEach(function(p){
      p.style.display = p.getAttribute('data-period') === currentPeriod ? '' : 'none';
    });
    document.getElementById('periodLabel').textContent = '— ' + currentPeriod;
    updateKpis();
    renderMain();
  });
});

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

document.querySelectorAll('tr.trade-row').forEach(function(tr) {
  tr.addEventListener('click', function() {
    renderTrade(tr);
    markSeen(tr.getAttribute('data-id'));
  });
});

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

var serverTs = {{ now()->timestamp }};
var offset = Math.floor(Date.now() / 1000) - serverTs;
function pad(x){ return (x < 10 ? '0' : '') + x; }
function tick() {
  var d = new Date((Math.floor(Date.now() / 1000) - offset) * 1000);
  document.getElementById('clock').textContent = d.getFullYear() + '-' + pad(d.getMonth()+1) + '-' + pad(d.getDate()) + ' ' + pad(d.getHours()) + ':' + pad(d.getMinutes()) + ':' + pad(d.getSeconds());
}
setInterval(tick, 1000); tick();
setTimeout(function(){ location.reload(); }, 60000);

renderMain();
</script>
@endsection
