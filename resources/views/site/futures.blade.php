@extends('layouts.app')

@section('title', 'Чемпионат Шварца 7x — Монитор Счёта и Бэктеста')

@section('content')
@php
    $balance = $compare['wallet_usdt'] ?? ($signals['usdt_free'] ?? 1985.53);
    $positions = $signals['open_positions'] ?? 0;
    $btcRegime = $signals['btc_regime'] ?? '—';
    $btcPrice = $signals['btc_price'] ?? null;
    $liveCoins = $signals['coins'] ?? [];
    $periods = $compare['periods'] ?? [];
    $lastRun = $compare['generated_at'] ?? ($signals['updated_at'] ?? '—');
    $cronSchedule = $compare['cron_schedule'] ?? 'Сверка счёта каждые 5 минут · Новые сигналы каждый час (:02)';
    $periodKeys = ['Квартал', 'Год', 'Вся история', 'Месяц'];
    $periodMap = [];
    foreach ($periods as $p) { $periodMap[$p['label'] ?? ''] = $p; }
    $active = $periodMap['Квартал'] ?? ($periods[0] ?? []);

    $chartData = [];
    $periodStats = [];
    foreach ($periods as $p) {
        $lbl = $p['label'] ?? '';
        // Backtest curve
        $btLabels = []; $btPnl = []; $btEq = [];
        if (!empty($p['equity'])) {
            foreach ($p['equity'] as $pt) {
                $btLabels[] = substr($pt[0], 0, 10);
                $btPnl[] = $pt[1];
                $btEq[] = round(2000 * (1 + ($pt[1] / 100)), 2);
            }
        }
        // Real account curve
        $real = $p['real_account'] ?? [];
        $rLabels = []; $rPnl = []; $rBal = [];
        foreach ($real['curve'] ?? [] as $rpt) {
            $rLabels[] = substr($rpt[0], 5, 11);
            $rPnl[] = $rpt[1];
            $rBal[] = $rpt[2];
        }
        $chartData[$lbl] = [
            'real_labels' => $rLabels,
            'real_pnl' => $rPnl,
            'real_bal' => $rBal,
            'bt_labels' => $btLabels,
            'bt_pnl' => $btPnl,
            'bt_eq' => $btEq,
        ];
        $periodStats[$lbl] = [
            'bt_total' => $p['total'] ?? 0,
            'bt_wins' => $p['wins'] ?? 0,
            'bt_wr' => $p['win_rate'] ?? 0,
            'bt_ret' => $p['return_pct'] ?? 0,
            'bt_dd' => $p['max_dd_pct'] ?? 0,
            'bt_final' => $p['final_eq'] ?? 2000,
            'matched' => $p['matched'] ?? 0,
            'real_start' => $real['start_balance'] ?? $balance,
            'real_end' => $real['end_balance'] ?? $balance,
            'real_pnl_usd' => $real['pnl_usd'] ?? 0,
            'real_ret' => $real['return_pct'] ?? 0,
            'real_trades' => $real['trades_count'] ?? 0,
            'real_wins' => $real['wins_count'] ?? 0,
        ];
    }
    $tradePaths = [];
    foreach ($periods as $p) {
        foreach ($p['trades'] ?? [] as $t) {
            $tid = md5(($t['time'] ?? '') . ($t['coin'] ?? '') . ($t['side'] ?? '') . ($t['entry_price'] ?? ''));
            $tradePaths[$tid] = $t['path'] ?? null;
        }
    }
    $actSt = $periodStats['Квартал'] ?? reset($periodStats) ?: [];
@endphp

<style>
    body { overflow: hidden; }
    .container { max-width: 100% !important; padding: 8px 16px 10px !important; }
    .monitor { display: flex; flex-direction: column; height: calc(100vh - 58px); gap: 8px; }
    .topbar { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 10px; background: #0f1624; border: 1px solid #222d45; border-radius: 10px; padding: 8px 14px; }
    .clock { font-size: 14px; font-weight: 700; color: #10b981; font-variant-numeric: tabular-nums; }
    .live-dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: #10b981; animation: pulse 2s infinite; margin-right: 5px; }
    @keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.25; } }
    .tabs { display: flex; gap: 6px; }
    .tab { padding: 5px 13px; border-radius: 7px; border: 1px solid #222d45; background: #131a2a; color: #94a3b8; cursor: pointer; font-size: 12.5px; font-weight: 600; transition: .15s; }
    .tab.active { background: #10b981; color: #052e16; border-color: #10b981; }
    .mode-switch { display: flex; gap: 6px; background: #0b0f19; padding: 3px; border-radius: 8px; border: 1px solid #222d45; }
    .mbtn { padding: 5px 12px; border-radius: 6px; border: none; background: transparent; color: #94a3b8; cursor: pointer; font-size: 12.5px; font-weight: 600; }
    .mbtn.active-real { background: #10b981; color: #052e16; }
    .mbtn.active-bt { background: #f59e0b; color: #1e1303; }
    .summary-row { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
    .summary-box { background: #0f1624; border: 1px solid #222d45; border-radius: 10px; padding: 8px 14px; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 10px; }
    .summary-box.real-box { border-left: 4px solid #10b981; }
    .summary-box.bt-box { border-left: 4px solid #f59e0b; }
    .s-title { font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: .04em; }
    .s-metrics { display: flex; gap: 18px; flex-wrap: wrap; }
    .sm .l { font-size: 10px; color: #94a3b8; text-transform: uppercase; }
    .sm .v { font-size: 16px; font-weight: 700; color: #fff; font-variant-numeric: tabular-nums; }
    .checklist-bar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; background: #0f1624; border: 1px solid #222d45; border-radius: 8px; padding: 5px 12px; font-size: 11.5px; }
    .coin-chip { padding: 2px 7px; border-radius: 5px; background: #162036; border: 1px solid #283655; font-size: 11px; }
    .main { display: grid; grid-template-columns: 1.45fr 1fr; gap: 12px; flex: 1; min-height: 0; }
    .panel { background: #131a2a; border: 1px solid #222d45; border-radius: 10px; padding: 10px 12px; overflow: auto; display: flex; flex-direction: column; }
    .panel h2 { font-size: 12.5px; color: #94a3b8; text-transform: uppercase; letter-spacing: .05em; margin-bottom: 8px; display: flex; justify-content: space-between; align-items: center; gap: 8px; }
    table { border-collapse: collapse; width: 100%; font-size: 12px; }
    td, th { padding: 5px 7px; border-bottom: 1px solid #222d45; text-align: left; white-space: nowrap; }
    th { color: #94a3b8; font-size: 10px; text-transform: uppercase; letter-spacing: .03em; position: sticky; top: 0; background: #131a2a; z-index: 1; }
    tr.trade-row { cursor: pointer; }
    tr.matched td { background: rgba(16,185,129,0.09); }
    tr.missed td { background: rgba(239,68,68,0.07); }
    .ok { color: #10b981; font-weight: 700; } .bad { color: #ef4444; font-weight: 700; } .warn { color: #f59e0b; font-weight: 700; }
    .muted { color: #94a3b8; }
    .badge-rule { padding: 2px 6px; border-radius: 4px; font-size: 10px; font-weight: 600; background: #1e293b; color: #e2e8f0; }
    .badge-pyr { padding: 1px 5px; border-radius: 4px; font-size: 10px; font-weight: 700; background: rgba(245,158,11,0.2); color: #fbbf24; margin-left: 4px; }
    .chart-wrap { flex: 1; min-height: 220px; position: relative; }
    .close-x { cursor: pointer; color: #ef4444; font-weight: 700; border: 1px solid #ef4444; border-radius: 6px; padding: 2px 8px; font-size: 11.5px; background: transparent; }
</style>

<div class="monitor">
  <div class="topbar">
    <div style="display:flex; align-items:center; gap:14px; flex-wrap:wrap">
      <h1 style="font-size:15px; margin:0">Чемпионат Шварца 7x · Сверка Счёта и Стратегии</h1>
      <div class="mode-switch">
        <button class="mbtn active-real" id="modeRealBtn">1. Реальный счёт Bybit</button>
        <button class="mbtn" id="modeBtBtn">2. Расчёт стратегии (Бэктест)</button>
      </div>
      <div class="tabs" id="periodTabs">
        @foreach ($periodKeys as $k)
        @if (isset($periodMap[$k]))
        <div class="tab {{ $k === 'Квартал' ? 'active' : '' }}" data-period="{{ $k }}">{{ $k }}</div>
        @endif
        @endforeach
      </div>
    </div>
    <div style="display:flex; align-items:center; gap:14px; font-size:12px">
      <span title="{{ $cronSchedule }}"><span class="live-dot"></span><b>Крон:</b> каждые 5 мин (сверка: {{ substr($lastRun, 0, 16) }})</span>
      <span class="muted">UTC: <span id="clock" class="clock">{{ now()->format('H:i:s') }}</span></span>
    </div>
  </div>

  <div class="summary-row">
    <div class="summary-box real-box">
      <div>
        <div class="s-title" style="color:#10b981">1. Факт по реальному счёту Bybit</div>
        <div class="muted" style="font-size:11px">Реальные закрытые позиции и баланс кошелька</div>
      </div>
      <div class="s-metrics">
        <div class="sm"><div class="l">Текущий баланс</div><div class="v">${{ number_format($balance, 2) }}</div></div>
        <div class="sm"><div class="l">Открыто сейчас</div><div class="v">{{ $positions }} поз.</div></div>
        <div class="sm"><div class="l">PnL счёта (<span class="periodName">Квартал</span>)</div><div class="v {{ ($actSt['real_pnl_usd'] ?? 0) >= 0 ? 'ok' : 'bad' }}" id="realPnlUsd">{{ ($actSt['real_pnl_usd'] ?? 0) >= 0 ? '+$' : '-$' }}{{ number_format(abs($actSt['real_pnl_usd'] ?? 0), 2) }} (<span id="realRetPct">{{ ($actSt['real_ret'] ?? 0) >= 0 ? '+' : '' }}{{ number_format($actSt['real_ret'] ?? 0, 2) }}%</span>)</div></div>
        <div class="sm"><div class="l">Закрыто сделок счёта</div><div class="v"><span id="realWins">{{ $actSt['real_wins'] ?? 0 }}</span> побед / <span id="realTrades">{{ $actSt['real_trades'] ?? 0 }}</span></div></div>
        <div class="sm"><div class="l">Сделок по Шварцу</div><div class="v ok" id="realMatched">{{ $actSt['matched'] ?? 0 }}</div></div>
      </div>
    </div>

    <div class="summary-box bt-box">
      <div>
        <div class="s-title" style="color:#f59e0b">2. Эталон стратегии (Бэктест с $2,000)</div>
        <div class="muted" style="font-size:11px">Как отработала модель Шварца 7x за выбранный период</div>
      </div>
      <div class="s-metrics">
        <div class="sm"><div class="l">Доходность (<span class="periodName">Квартал</span>)</div><div class="v {{ ($actSt['bt_ret'] ?? 0) >= 0 ? 'ok' : 'bad' }}" id="btRetPct">{{ ($actSt['bt_ret'] ?? 0) >= 0 ? '+' : '' }}{{ number_format($actSt['bt_ret'] ?? 0, 2) }}%</div></div>
        <div class="sm"><div class="l">Итог с $2,000</div><div class="v" id="btFinalEq">${{ number_format($actSt['bt_final'] ?? 2000, 0) }}</div></div>
        <div class="sm"><div class="l">Побед / Сделок</div><div class="v"><span id="btWins">{{ $actSt['bt_wins'] ?? 0 }}</span> / <span id="btTotal">{{ $actSt['bt_total'] ?? 0 }}</span></div></div>
        <div class="sm"><div class="l">Макс. просадка</div><div class="v warn" id="btDdPct">{{ number_format($actSt['bt_dd'] ?? 0, 1) }}%</div></div>
      </div>
    </div>
  </div>

  <div class="checklist-bar">
    <b style="color:#38bdf8">Режим рынка BTC: {{ $btcPrice ? '$' . number_format($btcPrice, 0) . ' · ' : '' }}{{ $btcRegime }}</b>
    <span class="muted">|</span>
    @foreach ($liveCoins as $c)
      <span class="coin-chip">
        <b>{{ $c['coin'] }}</b>: {{ $c['price'] }}
        <span class="{{ strpos($c['trend'], 'BULL') !== false ? 'ok' : 'bad' }}">{{ strpos($c['trend'], 'BULL') !== false ? '▲ >10d' : '▼ <10d' }}</span>
        <span class="muted">(ATR {{ $c['atr_pct'] }}%)</span>
      </span>
    @endforeach
  </div>

  <div class="main">
    <div class="panel" id="tablePanel">
      <!-- БЛОК 1: ТАБЛИЦА РЕАЛЬНОГО СЧЁТА -->
      <div id="realTableSection">
        <h2 style="color:#10b981">
          <span>Реальные закрытые сделки на счёте Bybit — <span class="periodName">Квартал</span></span>
          <span class="muted" style="text-transform:none">Прямая выгрузка закрытых позиций со счёта Bybit</span>
        </h2>
        @foreach ($periods as $p)
        @php $rClosed = $p['real_account']['closed_trades'] ?? []; @endphp
        <div class="real-period-table" data-period="{{ $p['label'] }}" style="{{ $p['label'] === 'Квартал' ? '' : 'display:none' }}">
          @if (empty($rClosed))
            <p class="muted">За этот период закрытых позиций на счёте Bybit нет.</p>
          @else
          <table>
            <tr><th>Открытие → Закрытие</th><th>Пара</th><th>Сторона</th><th>Цена входа → выхода</th><th>Объём (Плечо)</th><th>Источник</th><th>Реальный PnL ($)</th><th>Баланс счёта</th></tr>
            @foreach ($rClosed as $rc)
            @php
              $rp = $rc['pnl_usd'] ?? 0;
              $isSchw = !empty($rc['is_schwartz']);
            @endphp
            <tr class="{{ $rp >= 0 ? 'matched' : 'missed' }}">
              <td>{{ substr($rc['time'], 5) }} → {{ substr($rc['exit_time'], 5) }}</td>
              <td><b>{{ $rc['coin'] }}</b></td>
              <td class="{{ ($rc['side'] ?? 1) == 1 ? 'ok' : 'bad' }}">{{ ($rc['side'] ?? 1) == 1 ? 'LONG' : 'SHORT' }}</td>
              <td>{{ $rc['entry_price'] }} → {{ $rc['exit_price'] }}</td>
              <td>{{ $rc['qty'] }} <span class="muted">({{ $rc['leverage'] }}x)</span></td>
              <td><span class="badge-rule {{ $isSchw ? 'ok' : '' }}">{{ $isSchw ? '✓ По Шварцу 7x' : 'Прежние сделки счёта' }}</span></td>
              <td class="{{ $rp >= 0 ? 'ok' : 'bad' }}">{{ $rp >= 0 ? '+$' : '-$' }}{{ number_format(abs($rp), 2) }} ({{ ($rc['pnl_pct'] ?? 0) >= 0 ? '+' : '' }}{{ number_format($rc['pnl_pct'] ?? 0, 2) }}%)</td>
              <td style="font-weight:700">${{ number_format($rc['eq_after'] ?? $balance, 2) }}</td>
            </tr>
            @endforeach
          </table>
          @endif
        </div>
        @endforeach
      </div>

      <!-- БЛОК 2: ТАБЛИЦА БЭКТЕСТА -->
      <div id="btTableSection" style="display:none">
        <h2 style="color:#f59e0b">
          <span>Сделки модели в бэктесте (клик по строке открывает часовой график сделки) — <span class="periodName">Квартал</span></span>
        </h2>
        @foreach ($periods as $p)
        <div class="bt-period-table" data-period="{{ $p['label'] }}" style="{{ $p['label'] === 'Квартал' ? '' : 'display:none' }}">
          @if (empty($p['trades']))
            <p class="muted">Нет сделок бэктеста за выбранный период.</p>
          @else
          <table>
            <tr><th>Вход</th><th>Выход</th><th>Пара</th><th>Сторона</th><th>Цена входа → выхода</th><th>Маржа</th><th>Правило выхода</th><th>Статус</th><th>PnL бэктеста</th><th>Баланс ($2k старт)</th></tr>
            @foreach ($p['trades'] as $t)
            @php
              $pnl = $t['pnl_pct'] ?? 0;
              $st = $t['status'] ?? 'BACKTEST';
              $rowCls = $pnl >= 0 ? 'matched' : 'missed';
              $tid = md5(($t['time'] ?? '') . ($t['coin'] ?? '') . ($t['side'] ?? '') . ($t['entry_price'] ?? ''));
            @endphp
            <tr class="trade-row {{ $rowCls }}" data-id="{{ $tid }}" data-coin="{{ $t['coin'] }}" data-side="{{ $t['side'] }}" data-entry="{{ $t['entry_price'] }}" data-exit="{{ $t['exit_price'] }}" data-time="{{ $t['time'] }}" data-exittime="{{ $t['exit_time'] }}" data-pnl="{{ $pnl }}">
              <td>{{ substr($t['time'], 5) }}</td>
              <td>{{ substr($t['exit_time'] ?? '—', 5) }} <span class="muted">({{ $t['hold_h'] ?? 24 }}h)</span></td>
              <td><b>{{ $t['coin'] }}</b>@if(!empty($t['pyr']))<span class="badge-pyr">+PYR</span>@endif</td>
              <td class="{{ ($t['side'] ?? 0) == 1 ? 'ok' : 'bad' }}">{{ ($t['side'] ?? 0) == 1 ? 'LONG' : 'SHORT' }}</td>
              <td>{{ $t['entry_price'] ?? '—' }} → {{ $t['exit_price'] ?? '—' }}</td>
              <td>${{ number_format($t['margin'] ?? 0, 0) }} <span class="muted">({{ $t['eff_lev'] ?? 7 }}x)</span></td>
              <td><span class="badge-rule">{{ $t['reason'] ?? 'end' }}</span></td>
              <td class="{{ $st === 'MATCHED' ? 'ok' : 'warn' }}">{{ $st === 'MATCHED' ? '✓ Исполнено' : 'Только бэктест' }}</td>
              <td class="{{ $pnl >= 0 ? 'ok' : 'bad' }}">{{ $pnl >= 0 ? '+' : '' }}{{ number_format($pnl, 2) }}% <span class="muted">(${{ number_format($t['pnl_usd'] ?? 0, 0) }})</span></td>
              <td style="font-weight:700">${{ number_format($t['eq'] ?? 2000, 0) }}</td>
            </tr>
            @endforeach
          </table>
          @endif
        </div>
        @endforeach
      </div>
    </div>

    <div class="panel">
      <h2>
        <span id="chartTitle">Реальный баланс счёта Bybit ($) — Квартал</span>
        <button class="close-x" id="btnClose" style="display:none">✕ вернуться к графику периода</button>
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
var viewMode = 'real'; // 'real' (Реальный счёт) or 'bt' (Бэктест)
var currentPeriod = 'Квартал';

function updateKpis() {
  var st = periodStats[currentPeriod];
  if (!st) return;
  document.querySelectorAll('.periodName').forEach(function(el){ el.textContent = currentPeriod; });

  var rp = Number(st.real_pnl_usd || 0);
  var rr = Number(st.real_ret || 0);
  var rpEl = document.getElementById('realPnlUsd');
  rpEl.className = 'v ' + (rp >= 0 ? 'ok' : 'bad');
  rpEl.innerHTML = (rp >= 0 ? '+$' : '-$') + Math.abs(rp).toFixed(2) + ' (' + (rr >= 0 ? '+' : '') + rr.toFixed(2) + '%)';
  document.getElementById('realWins').textContent = st.real_wins;
  document.getElementById('realTrades').textContent = st.real_trades;
  document.getElementById('realMatched').textContent = st.matched;

  var br = Number(st.bt_ret || 0);
  var brEl = document.getElementById('btRetPct');
  brEl.className = 'v ' + (br >= 0 ? 'ok' : 'bad');
  brEl.textContent = (br >= 0 ? '+' : '') + br.toFixed(2) + '%';
  document.getElementById('btFinalEq').textContent = '$' + Number(st.bt_final).toLocaleString('en-US', {maximumFractionDigits: 0});
  document.getElementById('btWins').textContent = st.bt_wins;
  document.getElementById('btTotal').textContent = st.bt_total;
  document.getElementById('btDdPct').textContent = Number(st.bt_dd).toFixed(1) + '%';
}

function syncTables() {
  document.getElementById('realTableSection').style.display = viewMode === 'real' ? '' : 'none';
  document.getElementById('btTableSection').style.display = viewMode === 'bt' ? '' : 'none';
  document.querySelectorAll('.real-period-table').forEach(function(p){
    p.style.display = p.getAttribute('data-period') === currentPeriod ? '' : 'none';
  });
  document.querySelectorAll('.bt-period-table').forEach(function(p){
    p.style.display = p.getAttribute('data-period') === currentPeriod ? '' : 'none';
  });
}

function renderMain() {
  var ctx = document.getElementById('mainChart');
  var d = chartData[currentPeriod] || { real_labels: [], real_bal: [], bt_labels: [], bt_pnl: [] };
  if (CHART) { CHART.destroy(); }

  if (viewMode === 'real') {
    CHART = new Chart(ctx, {
      type: 'line',
      data: {
        labels: d.real_labels,
        datasets: [{
          label: 'Реальный баланс Bybit ($)',
          data: d.real_bal,
          borderColor: '#10b981',
          backgroundColor: 'rgba(16,185,129,0.12)',
          fill: true,
          tension: 0.15,
          pointRadius: 4,
          pointBackgroundColor: '#10b981'
        }]
      },
      options: {
        responsive: true, maintainAspectRatio: false, animation: false,
        plugins: {
          legend: { display: false },
          tooltip: { callbacks: { label: function(c){ return 'Реальный баланс: $' + c.parsed.y.toFixed(2) + ' (PnL: ' + (d.real_pnl[c.dataIndex] >= 0 ? '+' : '') + d.real_pnl[c.dataIndex] + '%)'; } } }
        },
        scales: {
          x: { ticks: { color: '#94a3b8', maxTicksLimit: 8, font: { size: 10 } }, grid: { color: '#222d45' } },
          y: { ticks: { color: '#94a3b8', font: { size: 10 }, callback: function(v){ return '$' + v; } }, grid: { color: '#222d45' } }
        }
      }
    });
    document.getElementById('chartTitle').textContent = '1. Реальный баланс и PnL счёта Bybit ($) — ' + currentPeriod;
  } else {
    CHART = new Chart(ctx, {
      type: 'line',
      data: {
        labels: d.bt_labels,
        datasets: [{
          label: 'Бэктест PnL (%)',
          data: d.bt_pnl,
          borderColor: '#f59e0b',
          backgroundColor: 'rgba(245,158,11,0.12)',
          fill: true,
          tension: 0.15,
          pointRadius: 2
        }]
      },
      options: {
        responsive: true, maintainAspectRatio: false, animation: false,
        plugins: {
          legend: { display: false },
          tooltip: { callbacks: { label: function(c){ return 'Бэктест: ' + (c.parsed.y >= 0 ? '+' : '') + c.parsed.y + '% ($' + d.bt_eq[c.dataIndex] + ')'; } } }
        },
        scales: {
          x: { ticks: { color: '#94a3b8', maxTicksLimit: 8, font: { size: 10 } }, grid: { color: '#222d45' } },
          y: { ticks: { color: '#94a3b8', font: { size: 10 }, callback: function(v){ return (v >= 0 ? '+' : '') + v + '%'; } }, grid: { color: '#222d45' } }
        }
      }
    });
    document.getElementById('chartTitle').textContent = '2. Расчётная кривая стратегии (Бэктест %) — ' + currentPeriod;
  }
}

function renderTrade(tr) {
  var id = tr.getAttribute('data-id');
  var coin = tr.getAttribute('data-coin');
  var side = tr.getAttribute('data-side') === '1' ? 'LONG' : 'SHORT';
  var entry = parseFloat(tr.getAttribute('data-entry'));
  var exit = parseFloat(tr.getAttribute('data-exit'));
  var pnl = parseFloat(tr.getAttribute('data-pnl'));
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
          pointBackgroundColor: colors
        }]
      },
      options: {
        responsive: true, maintainAspectRatio: false, animation: false,
        plugins: {
          legend: { display: false },
          title: { display: true, text: coin + ' ' + side + ' · вход ' + entry + ' → выход ' + exit + ' · PnL ' + (pnl >= 0 ? '+' : '') + pnl + '%', color: '#e2e8f0', font: { size: 12 } }
        },
        scales: {
          x: { ticks: { color: '#94a3b8', font: { size: 10 } }, grid: { color: '#222d45' } },
          y: { ticks: { color: '#94a3b8', font: { size: 10 } }, grid: { color: '#222d45' } }
        }
      }
    });
  }
  document.getElementById('chartTitle').textContent = 'Сделка бэктеста: ' + coin + ' ' + side;
  document.getElementById('btnClose').style.display = '';
}

document.getElementById('modeRealBtn').addEventListener('click', function() {
  viewMode = 'real';
  this.className = 'mbtn active-real';
  document.getElementById('modeBtBtn').className = 'mbtn';
  document.getElementById('btnClose').style.display = 'none';
  syncTables();
  renderMain();
});

document.getElementById('modeBtBtn').addEventListener('click', function() {
  viewMode = 'bt';
  this.className = 'mbtn active-bt';
  document.getElementById('modeRealBtn').className = 'mbtn';
  document.getElementById('btnClose').style.display = 'none';
  syncTables();
  renderMain();
});

document.querySelectorAll('#periodTabs .tab').forEach(function(tab) {
  tab.addEventListener('click', function() {
    currentPeriod = tab.getAttribute('data-period');
    document.querySelectorAll('#periodTabs .tab').forEach(function(t){ t.classList.remove('active'); });
    tab.classList.add('active');
    document.getElementById('btnClose').style.display = 'none';
    updateKpis();
    syncTables();
    renderMain();
  });
});

document.getElementById('btnClose').addEventListener('click', function() {
  this.style.display = 'none';
  renderMain();
});

document.querySelectorAll('tr.trade-row').forEach(function(tr) {
  tr.addEventListener('click', function() {
    renderTrade(tr);
  });
});

var serverTs = {{ now()->timestamp }};
var offset = Math.floor(Date.now() / 1000) - serverTs;
function pad(x){ return (x < 10 ? '0' : '') + x; }
function tick() {
  var d = new Date((Math.floor(Date.now() / 1000) - offset) * 1000);
  document.getElementById('clock').textContent = pad(d.getUTCHours()) + ':' + pad(d.getUTCMinutes()) + ':' + pad(d.getUTCSeconds());
}
setInterval(tick, 1000); tick();
setTimeout(function(){ location.reload(); }, 60000);

updateKpis();
syncTables();
renderMain();
</script>
@endsection
