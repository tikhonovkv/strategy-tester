@extends('layouts.app')

@section('title', 'Чемпионат Шварца — Монитор Счетов и Бэктеста')

@section('content')
@php
    $activeAcc = $activeAccount ?? [
        'id' => 'main',
        'name' => 'Основной Bybit (7x)',
        'exchange' => 'bybit',
        'strategy' => 'schwartz_7x_sleeve_3x',
        'leverage' => 7.0,
        'sleeve_leverage' => 3.0,
        'risk_pct' => 0.075,
        'max_margin_frac' => 0.60,
        'max_conc' => 2,
        'enable_sleeve' => true,
        'enabled' => true,
        'pause_new_trades' => false,
    ];
    $accList = $accounts ?? [$activeAcc];
    $allAccList = $allAccounts ?? $accList;
    $masterMode = (bool) ($isMaster ?? false);
    $accLev = (float) ($activeAcc['leverage'] ?? ($compare['leverage'] ?? 7.0));
    $slvLev = (float) ($activeAcc['sleeve_leverage'] ?? ($compare['sleeve_leverage'] ?? 3.0));
    $calcState = $calcStatus ?? [];
    $calculatingNow = (bool) ($isCalculating ?? false);

    $balance = $compare['wallet_usdt'] ?? ($signals['account_equity'] ?? ($signals['usdt_free'] ?? 0));
    $positions = $signals['open_positions'] ?? 0;
    $sleevePositions = $signals['sleeve_positions'] ?? 0;
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

    $strategyLabels = [
        'schwartz_7x_sleeve_3x' => 'Шварц + Корзина',
        'schwartz_only' => 'Только Шварц',
        'sleeve_only' => 'Только Корзина',
    ];

    $chartData = [];
    $periodStats = [];
    foreach ($periods as $p) {
        $lbl = $p['label'] ?? '';
        $btLabels = []; $btPnl = []; $btEq = [];
        if (!empty($p['equity'])) {
            foreach ($p['equity'] as $pt) {
                $btLabels[] = substr($pt[0], 0, 10);
                $btPnl[] = $pt[1];
                $btEq[] = round(2000 * (1 + ($pt[1] / 100)), 2);
            }
        }
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
            'real_transfers_usd' => $real['net_transfers_usd'] ?? 0,
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
    header { display: none !important; }
    body { overflow: hidden; background: #0b0f19; }
    .container { max-width: 100% !important; padding: 8px 14px 10px !important; margin: 0 !important; }
    .monitor { display: flex; flex-direction: column; height: calc(100vh - 18px); gap: 7px; }

    /* Компактная верхняя панель */
    .topbar {
        display: flex;
        justify-content: space-between;
        align-items: center;
        flex-wrap: wrap;
        gap: 8px;
        background: #0f1624;
        border: 1px solid #222d45;
        border-radius: 10px;
        padding: 6px 12px;
    }
    .topbar-left, .topbar-right {
        display: flex;
        align-items: center;
        gap: 7px;
        flex-wrap: wrap;
    }
    .clock { font-size: 13px; font-weight: 700; color: #10b981; font-variant-numeric: tabular-nums; }
    .live-dot { display: inline-block; width: 7px; height: 7px; border-radius: 50%; background: #10b981; animation: pulse 2s infinite; margin-right: 4px; }
    @keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.25; } }
    @keyframes spin { to { transform: rotate(360deg); } }
    .spinner { display: inline-block; width: 14px; height: 14px; border: 2px solid rgba(251,191,36,0.3); border-top-color: #fbbf24; border-radius: 50%; animation: spin 0.9s linear infinite; vertical-align: middle; margin-right: 6px; }

    .acc-tabs { display: flex; gap: 4px; align-items: center; background: #0b0f19; padding: 3px; border-radius: 8px; border: 1px solid #222d45; }
    .acc-tab { padding: 4px 9px; border-radius: 6px; font-size: 11.5px; font-weight: 600; color: #94a3b8; text-decoration: none; display: inline-flex; align-items: center; gap: 4px; white-space: nowrap; }
    .acc-tab.active { background: #3b82f6; color: #fff; }
    .acc-tab.off { opacity: 0.55; }

    .mode-switch { display: flex; gap: 3px; background: #0b0f19; padding: 3px; border-radius: 8px; border: 1px solid #222d45; }
    .mbtn { padding: 4px 10px; border-radius: 6px; border: none; background: transparent; color: #94a3b8; cursor: pointer; font-size: 11.5px; font-weight: 600; white-space: nowrap; }
    .mbtn.active-real { background: #10b981; color: #052e16; }
    .mbtn.active-bt { background: #f59e0b; color: #1e1303; }

    .tabs { display: flex; gap: 4px; }
    .tab { padding: 4px 10px; border-radius: 6px; border: 1px solid #222d45; background: #131a2a; color: #94a3b8; cursor: pointer; font-size: 11.5px; font-weight: 600; transition: .15s; text-decoration: none; white-space: nowrap; }
    .tab.active { background: #10b981; color: #052e16; border-color: #10b981; }

    .btn-sm { padding: 4px 9px; border-radius: 6px; font-size: 11.5px; font-weight: 600; cursor: pointer; text-decoration: none; display: inline-flex; align-items: center; gap: 4px; white-space: nowrap; border: 1px solid #283655; background: #162036; color: #e2e8f0; }
    .btn-sm:hover { background: #1e2b47; }
    .btn-pause-off { border-color: #f59e0b; color: #fbbf24; background: rgba(245,158,11,0.1); }
    .btn-pause-on { border-color: #f59e0b; background: #f59e0b; color: #1e1303; font-weight: 700; }

    /* Компактные и ровные карточки сводки */
    .summary-row { display: grid; grid-template-columns: 1.15fr 0.85fr; gap: 8px; }
    .summary-box { background: #0f1624; border: 1px solid #222d45; border-radius: 10px; padding: 7px 11px; display: flex; flex-direction: column; gap: 5px; min-width: 0; }
    .summary-box.real-box { border-left: 4px solid #10b981; }
    .summary-box.bt-box { border-left: 4px solid #f59e0b; }
    .s-head { display: flex; justify-content: space-between; align-items: baseline; gap: 8px; flex-wrap: wrap; }
    .s-title { font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: .03em; white-space: nowrap; }
    .s-sub { font-size: 10.5px; color: #94a3b8; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .s-grid-6 { display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); gap: 6px; }
    .s-grid-5 { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 6px; }
    .sm { background: #131c2e; border: 1px solid #1e2942; border-radius: 6px; padding: 4px 7px; min-width: 0; }
    .sm .l { font-size: 9.5px; color: #94a3b8; text-transform: uppercase; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .sm .v { font-size: 13px; font-weight: 700; color: #fff; font-variant-numeric: tabular-nums; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; margin-top: 1px; }

    .checklist-bar { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; background: #0f1624; border: 1px solid #222d45; border-radius: 8px; padding: 4px 10px; font-size: 11px; }
    .coin-chip { padding: 1px 6px; border-radius: 4px; background: #162036; border: 1px solid #283655; font-size: 10.5px; white-space: nowrap; }

    .calc-banner {
        background: rgba(245, 158, 11, 0.14);
        border: 1px solid #f59e0b;
        color: #fef3c7;
        padding: 7px 12px;
        border-radius: 8px;
        font-size: 12px;
        display: flex;
        justify-content: space-between;
        align-items: center;
        gap: 10px;
    }

    .main { display: grid; grid-template-columns: 1.45fr 1fr; gap: 10px; flex: 1; min-height: 0; }
    .panel { background: #131a2a; border: 1px solid #222d45; border-radius: 10px; padding: 8px 10px; overflow: auto; display: flex; flex-direction: column; min-width: 0; }
    .panel h2 { font-size: 11.5px; color: #94a3b8; text-transform: uppercase; letter-spacing: .04em; margin-bottom: 6px; display: flex; justify-content: space-between; align-items: center; gap: 8px; flex-wrap: wrap; }
    table { border-collapse: collapse; width: 100%; font-size: 11.5px; }
    td, th { padding: 4px 6px; border-bottom: 1px solid #222d45; text-align: left; white-space: nowrap; }
    th { color: #94a3b8; font-size: 9.5px; text-transform: uppercase; letter-spacing: .03em; position: sticky; top: 0; background: #131a2a; z-index: 1; }
    tr.trade-row { cursor: pointer; }
    tr.matched td { background: rgba(16,185,129,0.09); }
    tr.missed td { background: rgba(239,68,68,0.07); }
    tr.transfer-in td { background: rgba(56,189,248,0.12); }
    tr.transfer-out td { background: rgba(245,158,11,0.12); }
    .ok { color: #10b981; font-weight: 700; } .bad { color: #ef4444; font-weight: 700; } .warn { color: #f59e0b; font-weight: 700; } .info { color: #38bdf8; font-weight: 700; }
    .muted { color: #94a3b8; }
    .badge-rule { padding: 2px 5px; border-radius: 4px; font-size: 9.5px; font-weight: 600; background: #1e293b; color: #e2e8f0; }
    .badge-transfer-in { padding: 2px 6px; border-radius: 4px; font-size: 9.5px; font-weight: 700; background: rgba(56,189,248,0.2); color: #38bdf8; }
    .badge-transfer-out { padding: 2px 6px; border-radius: 4px; font-size: 9.5px; font-weight: 700; background: rgba(245,158,11,0.2); color: #fbbf24; }
    .badge-pyr { padding: 1px 4px; border-radius: 4px; font-size: 9.5px; font-weight: 700; background: rgba(245,158,11,0.2); color: #fbbf24; margin-left: 3px; }
    .chart-wrap { flex: 1; min-height: 200px; position: relative; }
    .close-x { cursor: pointer; color: #ef4444; font-weight: 700; border: 1px solid #ef4444; border-radius: 6px; padding: 3px 8px; font-size: 11px; background: transparent; }

    /* Модальное окно управления счетами */
    .modal-backdrop { display: none; position: fixed; inset: 0; background: rgba(5,8,15,0.84); z-index: 100; align-items: center; justify-content: center; padding: 16px; }
    .modal-backdrop.open { display: flex; }
    .modal-box { background: #131a2a; border: 1px solid #2d3c5e; border-radius: 12px; width: 100%; max-width: 1060px; max-height: 92vh; overflow: auto; padding: 16px 20px; }
    .form-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; margin-top: 8px; }
    .form-grid label { display: flex; flex-direction: column; gap: 4px; font-size: 11px; color: #94a3b8; }
    .form-grid input, .form-grid select { background: #0b0f19; border: 1px solid #222d45; color: #e2e8f0; padding: 6px 9px; border-radius: 6px; font-size: 12px; width: 100%; }
    .masked-secret { -webkit-text-security: disc; text-security: disc; }
</style>

@if (session('status'))
<div style="background:rgba(16,185,129,0.18); border:1px solid #10b981; color:#d1fae5; padding:5px 12px; border-radius:8px; font-size:11.5px; margin-bottom:4px">
  {{ session('status') }}
</div>
@endif

<div class="monitor">
  <div class="topbar">
    <div class="topbar-left">
      <div class="acc-tabs" title="Переключение между счетами (ботами)">
        @foreach ($accList as $accItem)
          <a href="{{ url('/?account=' . urlencode($accItem['id'])) }}" class="acc-tab {{ ($accItem['id'] ?? '') === ($activeAcc['id'] ?? '') ? 'active' : '' }} {{ empty($accItem['enabled']) ? 'off' : '' }}">
            <span>{{ $accItem['name'] }}</span>
            <span style="font-size:10px; opacity:0.8">({{ number_format((float)($accItem['leverage'] ?? 7), 0) }}x)</span>
            @if (!empty($accItem['pause_new_trades']))
              <span style="color:#fbbf24; font-size:9.5px">⏸ ПАУЗА</span>
            @endif
            @if (empty($accItem['enabled']))
              <span style="color:#ef4444; font-size:9.5px">OFF</span>
            @endif
          </a>
        @endforeach
      </div>

      <form method="post" action="{{ route('accounts.action') }}" style="display:inline">
        @csrf
        <input type="hidden" name="id" value="{{ $activeAcc['id'] }}">
        <input type="hidden" name="action" value="toggle_pause">
        @if (!empty($activeAcc['pause_new_trades']))
          <button type="submit" class="btn-sm btn-pause-on" title="Сейчас новые сделки НЕ открываются (открытые позиции сопровождаются до закрытия). Нажмите, чтобы снова разрешить открытие сделок.">
            ▶ Новые сделки остановлены (Включить)
          </button>
        @else
          <button type="submit" class="btn-sm btn-pause-off" title="Остановить открытие новых сделок для счёта (уже открытые сделки спокойно доработают до закрытия)">
            ⏸ Не открывать сделок
          </button>
        @endif
      </form>

      <div class="mode-switch">
        <button class="mbtn active-real" id="modeRealBtn">1. Счёт Bybit</button>
        <button class="mbtn" id="modeBtBtn">2. Бэктест стратегии</button>
      </div>

      <div class="tabs" id="periodTabs">
        @foreach ($periodKeys as $k)
          @if (isset($periodMap[$k]))
            <div class="tab {{ $k === 'Квартал' ? 'active' : '' }}" data-period="{{ $k }}">{{ $k }}</div>
          @endif
        @endforeach
      </div>
    </div>

    <div class="topbar-right">
      <a href="{{ route('bot', ['account' => $activeAcc['id']]) }}" class="btn-sm">О стратегии</a>
      @if ($masterMode)
        <button type="button" class="btn-sm" id="btnOpenAccounts" style="border-color:#3b82f6; color:#93c5fd">⚙ Счета S3 ({{ count($allAccList) }})</button>
      @endif
      <span style="font-size:11px" title="{{ $cronSchedule }}"><span class="live-dot"></span>{{ substr($lastRun, 5, 11) ?: '—' }}</span>
      <span class="muted" style="font-size:11px">UTC <span id="clock" class="clock">{{ now()->format('H:i:s') }}</span></span>
      <form method="post" action="{{ route('logout') }}" style="display:inline">
        @csrf
        <button type="submit" class="btn-sm" style="padding:3px 7px; font-size:11px" title="Выйти или сменить пинкод">Пин ✕</button>
      </form>
    </div>
  </div>

  @if ($calculatingNow)
  <div class="calc-banner">
    <div>
      <span class="spinner"></span>
      <b>Идёт расчёт стратегии и синхронизация со счётом Bybit — пожалуйста, подождите 1–2 минуты.</b>
      <span class="muted" style="margin-left:6px">{{ $calcState['stage'] ?? 'Обновляются часовые свечи, переводы и сверка сделок...' }}</span>
    </div>
    <span style="font-size:11px; color:#fbbf24; white-space:nowrap">Автообновление каждые 15 сек</span>
  </div>
  @endif

  <div class="summary-row">
    <div class="summary-box real-box">
      <div class="s-head">
        <div class="s-title" style="color:#10b981">
          1. Счёт: {{ $activeAcc['name'] }} ({{ strtoupper($activeAcc['exchange'] ?? 'BYBIT') }} · Шварц {{ number_format($accLev, 0) }}x{{ !empty($activeAcc['enable_sleeve']) ? ' + Корзина ' . number_format($slvLev, 0) . 'x' : '' }})
          @if(!empty($activeAcc['pause_new_trades'])) <span class="warn">[ПАУЗА ВХОДОВ]</span> @endif
        </div>
        <div class="s-sub">Открытые позиции не увеличиваются при депозите · новый баланс со следующей сделки</div>
      </div>
      <div class="s-grid-6">
        <div class="sm"><div class="l">Баланс сейчас</div><div class="v">${{ number_format($balance, 2) }}</div></div>
        <div class="sm"><div class="l">Старт (<span class="periodName">Квартал</span>)</div><div class="v" id="realStartBal">${{ number_format($actSt['real_start'] ?? $balance, 2) }}</div></div>
        <div class="sm"><div class="l">Депозиты / Выводы</div><div class="v info" id="realTransfersUsd">{{ ($actSt['real_transfers_usd'] ?? 0) >= 0 ? '+$' : '-$' }}{{ number_format(abs($actSt['real_transfers_usd'] ?? 0), 2) }}</div></div>
        <div class="sm"><div class="l">Торговый PnL</div><div class="v {{ ($actSt['real_pnl_usd'] ?? 0) >= 0 ? 'ok' : 'bad' }}" id="realPnlUsd">{{ ($actSt['real_pnl_usd'] ?? 0) >= 0 ? '+$' : '-$' }}{{ number_format(abs($actSt['real_pnl_usd'] ?? 0), 2) }} (<span id="realRetPct">{{ ($actSt['real_ret'] ?? 0) >= 0 ? '+' : '' }}{{ number_format($actSt['real_ret'] ?? 0, 2) }}%</span>)</div></div>
        <div class="sm"><div class="l">Открыто сейчас</div><div class="v">{{ $positions }} поз.{{ $sleevePositions ? ' (+' . $sleevePositions . ')' : '' }}</div></div>
        <div class="sm"><div class="l">Побед / Сделок</div><div class="v"><span id="realWins">{{ $actSt['real_wins'] ?? 0 }}</span> / <span id="realTrades">{{ $actSt['real_trades'] ?? 0 }}</span></div></div>
      </div>
    </div>

    <div class="summary-box bt-box">
      <div class="s-head">
        <div class="s-title" style="color:#f59e0b">2. Эталон стратегии (старт $2,000)</div>
        <div class="s-sub">Риск {{ number_format(((float)($activeAcc['risk_pct'] ?? 0.075))*100, 1) }}% · маржа до {{ number_format(((float)($activeAcc['max_margin_frac'] ?? 0.60))*100, 0) }}% · макс. {{ $activeAcc['max_conc'] ?? 2 }} поз.</div>
      </div>
      <div class="s-grid-5">
        <div class="sm"><div class="l">Доходность</div><div class="v {{ ($actSt['bt_ret'] ?? 0) >= 0 ? 'ok' : 'bad' }}" id="btRetPct">{{ ($actSt['bt_ret'] ?? 0) >= 0 ? '+' : '' }}{{ number_format($actSt['bt_ret'] ?? 0, 2) }}%</div></div>
        <div class="sm"><div class="l">Итог с $2,000</div><div class="v" id="btFinalEq">${{ number_format($actSt['bt_final'] ?? 2000, 0) }}</div></div>
        <div class="sm"><div class="l">Побед / Сделок</div><div class="v"><span id="btWins">{{ $actSt['bt_wins'] ?? 0 }}</span> / <span id="btTotal">{{ $actSt['bt_total'] ?? 0 }}</span></div></div>
        <div class="sm"><div class="l">Совпало</div><div class="v ok" id="realMatched">{{ $actSt['matched'] ?? 0 }}</div></div>
        <div class="sm"><div class="l">Макс. просадка</div><div class="v warn" id="btDdPct">{{ number_format($actSt['bt_dd'] ?? 0, 1) }}%</div></div>
      </div>
    </div>
  </div>

  <div class="checklist-bar">
    <b style="color:#38bdf8">BTC: {{ $btcPrice ? '$' . number_format($btcPrice, 0) . ' · ' : '' }}{{ $btcRegime }}</b>
    <span class="muted">|</span>
    @foreach ($liveCoins as $c)
      <span class="coin-chip">
        <b>{{ $c['coin'] }}</b> {{ $c['price'] }}
        <span class="{{ strpos($c['trend'], 'BULL') !== false ? 'ok' : 'bad' }}">{{ strpos($c['trend'], 'BULL') !== false ? '▲' : '▼' }}</span>
        <span class="muted">ATR {{ $c['atr_pct'] }}%</span>
      </span>
    @endforeach
  </div>

  @if (empty($periods))
  <div class="panel" style="flex:1; align-items:center; justify-content:center; text-align:center; padding:40px">
    <div style="max-width:520px">
      <div style="font-size:28px; margin-bottom:10px">⏳</div>
      <h2 style="justify-content:center; color:#fbbf24; font-size:15px; margin-bottom:8px">Идёт расчёт и загрузка данных счёта «{{ $activeAcc['name'] }}»</h2>
      <p class="muted" style="font-size:13px; line-height:1.6">
        Пожалуйста, подождите 1–2 минуты. Планировщик обновляет свежие часовые свечи, рассчитывает эталон стратегии и сверяет сделки и пополнения со счётом Bybit.<br>
        Страница обновится автоматически, как только расчёт будет готов.
      </p>
    </div>
  </div>
  @else
  <div class="main">
    <div class="panel" id="tablePanel">
      <!-- БЛОК 1: ТАБЛИЦА РЕАЛЬНОГО СЧЁТА И ПЕРЕВОДОВ -->
      <div id="realTableSection">
        <h2 style="color:#10b981">
          <span>Сделки и пополнения счёта ({{ $activeAcc['name'] }}) — <span class="periodName">Квартал</span></span>
          <span class="muted" style="text-transform:none; font-size:10.5px">Выгрузка с Bybit (переводы не искажают % доходности)</span>
        </h2>
        @foreach ($periods as $p)
        @php $rClosed = $p['real_account']['closed_trades'] ?? []; @endphp
        <div class="real-period-table" data-period="{{ $p['label'] }}" style="{{ $p['label'] === 'Квартал' ? '' : 'display:none' }}">
          @if (empty($rClosed))
            <p class="muted">За этот период закрытых позиций и переводов на счёте нет.</p>
          @else
          <table>
            <tr><th>Время</th><th>Актив</th><th>Операция</th><th>Вход → Выход</th><th>Объём (Плечо)</th><th>Контур</th><th>Сумма / PnL ($)</th><th>Баланс</th></tr>
            @foreach ($rClosed as $rc)
            @if (!empty($rc['is_transfer']))
              @php
                $amt = (float) ($rc['amount_usd'] ?? 0);
                $isIn = $amt >= 0;
              @endphp
              <tr class="{{ $isIn ? 'transfer-in' : 'transfer-out' }}">
                <td>{{ substr($rc['exit_time'] ?? $rc['time'] ?? '', 5) }}</td>
                <td><b>USDT</b></td>
                <td class="{{ $isIn ? 'info' : 'warn' }}">{{ $isIn ? 'ПОПОЛНЕНИЕ' : 'ВЫВОД' }}</td>
                <td class="muted">Перевод средств</td>
                <td>{{ number_format(abs($amt), 2) }} USDT</td>
                <td><span class="{{ $isIn ? 'badge-transfer-in' : 'badge-transfer-out' }}">{{ $isIn ? '+ Депозит' : '− Вывод' }}</span></td>
                <td class="{{ $isIn ? 'info' : 'warn' }}">{{ $isIn ? '+$' : '-$' }}{{ number_format(abs($amt), 2) }}</td>
                <td style="font-weight:700">${{ number_format($rc['eq_after'] ?? $balance, 2) }}</td>
              </tr>
            @else
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
                <td><span class="badge-rule {{ $isSchw ? 'ok' : '' }}">{{ $isSchw ? ('Шварц ' . number_format($accLev, 0) . 'x') : (!empty($rc['is_sleeve']) ? ('Корзина ' . number_format($slvLev, 0) . 'x') : 'Вне бота') }}</span></td>
                <td class="{{ $rp >= 0 ? 'ok' : 'bad' }}">{{ $rp >= 0 ? '+$' : '-$' }}{{ number_format(abs($rp), 2) }} ({{ ($rc['pnl_pct'] ?? 0) >= 0 ? '+' : '' }}{{ number_format($rc['pnl_pct'] ?? 0, 2) }}%)</td>
                <td style="font-weight:700">${{ number_format($rc['eq_after'] ?? $balance, 2) }}</td>
              </tr>
            @endif
            @endforeach
          </table>
          @endif
        </div>
        @endforeach
      </div>

      <!-- БЛОК 2: ТАБЛИЦА БЭКТЕСТА -->
      <div id="btTableSection" style="display:none">
        <h2 style="color:#f59e0b">
          <span>Сделки по стратегии — <span class="periodName">Квартал</span></span>
          <span class="muted" style="text-transform:none; font-size:10.5px">Клик по строке покажет график цены сделки</span>
        </h2>
        @foreach ($periods as $p)
        <div class="bt-period-table" data-period="{{ $p['label'] }}" style="{{ $p['label'] === 'Квартал' ? '' : 'display:none' }}">
          @if (empty($p['trades']))
            <p class="muted">Нет сделок бэктеста за выбранный период.</p>
          @else
          <table>
            <tr><th>Вход</th><th>Выход</th><th>Контур</th><th>Пара</th><th>Сторона</th><th>Вход → Выход</th><th>Маржа ($2k) / Объём</th><th>Выход</th><th>Статус</th><th>PnL</th><th>Баланс</th></tr>
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
              <td>{{ ($t['book'] ?? '') === 'sleeve' ? 'Корзина' : 'Шварц' }}</td>
              <td><b>{{ $t['coin'] }}</b>@if(!empty($t['pyr']))<span class="badge-pyr">+PYR</span>@endif</td>
              <td class="{{ ($t['side'] ?? 0) == 1 ? 'ok' : 'bad' }}">{{ ($t['side'] ?? 0) == 1 ? 'LONG' : 'SHORT' }}</td>
              <td>{{ $t['entry_price'] ?? '—' }} → {{ $t['exit_price'] ?? '—' }}</td>
              <td>${{ number_format($t['margin'] ?? 0, 0) }} <span class="muted">({{ $t['eff_lev'] ?? $accLev }}x{{ !empty($t['scaled_qty']) ? ' · ' . $t['scaled_qty'] : '' }})</span></td>
              <td><span class="badge-rule">{{ $t['reason'] ?? 'end' }}</span></td>
              <td class="{{ $st === 'MATCHED' ? 'ok' : 'warn' }}">{{ $st === 'MATCHED' ? '✓ На счёте' : 'Бэктест' }}</td>
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
        <span id="chartTitle">Реальный баланс счёта ({{ $activeAcc['name'] }}) — Квартал</span>
        <button class="close-x" id="btnClose" style="display:none">✕ к графику периода</button>
      </h2>
      <div class="chart-wrap">
        <canvas id="mainChart"></canvas>
      </div>
    </div>
  </div>
  @endif
</div>

@if ($masterMode)
<div class="modal-backdrop" id="accountsModal">
  <div class="modal-box">
    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:10px">
      <div>
        <h2 style="font-size:15px; color:#fff; margin:0">Управление счетами и ботами (S3: accounts_config.json)</h2>
        <div class="muted" style="font-size:11.5px">Мастер-пин открывает все счета · индивидуальный пин открывает только свой счёт</div>
      </div>
      <button type="button" class="close-x" id="btnCloseAccounts">✕ Закрыть</button>
    </div>

    <table>
      <tr>
        <th>ID</th>
        <th>Название счёта</th>
        <th>Биржа / Стратегия</th>
        <th>Плечи и риск</th>
        <th>ПИН счёта</th>
        <th>API Ключ</th>
        <th>Режим</th>
        <th>Управление</th>
      </tr>
      @foreach ($allAccList as $a)
      <tr>
        <td><b>{{ $a['id'] }}</b></td>
        <td>{{ $a['name'] }}</td>
        <td>
          <b>{{ strtoupper($a['exchange'] ?? 'bybit') }}</b> ·
          <span class="muted">{{ $strategyLabels[$a['strategy'] ?? ''] ?? $a['strategy'] }}</span>
        </td>
        <td>
          Шварц <b>{{ number_format((float)($a['leverage'] ?? 7), 0) }}x</b> /
          Корз. <b>{{ number_format((float)($a['sleeve_leverage'] ?? 3), 0) }}x</b>
          <span class="muted">(риск {{ number_format(((float)($a['risk_pct'] ?? 0.075))*100, 1) }}%)</span>
        </td>
        <td><code>{{ $a['pin'] }}</code></td>
        <td><span class="{{ !empty($a['has_keys']) ? 'ok' : 'warn' }}">{{ $a['masked_key'] ?? '—' }}</span></td>
        <td>
          @if (!empty($a['enabled']))
            @if (!empty($a['pause_new_trades']))
              <span class="warn">⏸ ПАУЗА СДЕЛОК</span>
            @else
              <span class="ok">● АКТИВЕН</span>
            @endif
          @else
            <span class="bad">✕ ОТКЛЮЧЁН</span>
          @endif
        </td>
        <td>
          <div style="display:flex; gap:5px; align-items:center">
            <button type="button" class="btn-sm btn-edit-acc"
              data-id="{{ $a['id'] }}"
              data-name="{{ $a['name'] }}"
              data-exchange="{{ $a['exchange'] }}"
              data-strategy="{{ $a['strategy'] }}"
              data-leverage="{{ $a['leverage'] }}"
              data-sleeve_leverage="{{ $a['sleeve_leverage'] }}"
              data-risk_pct="{{ round(((float)$a['risk_pct'])*100, 2) }}"
              data-max_margin_frac="{{ round(((float)$a['max_margin_frac'])*100, 0) }}"
              data-max_conc="{{ $a['max_conc'] }}"
              data-pin="{{ $a['pin'] }}"
              data-enabled="{{ !empty($a['enabled']) ? '1' : '0' }}"
              data-pause_new_trades="{{ !empty($a['pause_new_trades']) ? '1' : '0' }}"
            >Изменить</button>
            <form method="post" action="{{ route('accounts.action') }}" style="display:inline">
              @csrf
              <input type="hidden" name="id" value="{{ $a['id'] }}">
              <input type="hidden" name="action" value="toggle_pause">
              <button type="submit" class="btn-sm btn-pause-off">{{ !empty($a['pause_new_trades']) ? '▶ Разрешить сделки' : '⏸ Пауза сделок' }}</button>
            </form>
            <form method="post" action="{{ route('accounts.action') }}" style="display:inline">
              @csrf
              <input type="hidden" name="id" value="{{ $a['id'] }}">
              <input type="hidden" name="action" value="toggle">
              <button type="submit" class="btn-sm">{{ !empty($a['enabled']) ? 'Выкл' : 'Вкл' }}</button>
            </form>
            @if (($a['id'] ?? '') !== 'main')
            <form method="post" action="{{ route('accounts.action') }}" style="display:inline" onsubmit="return confirm('Удалить аккаунт {{ $a['id'] }} из S3 конфига?')">
              @csrf
              <input type="hidden" name="id" value="{{ $a['id'] }}">
              <input type="hidden" name="action" value="delete">
              <button type="submit" class="btn-sm" style="color:#ef4444; border-color:#ef4444">✕</button>
            </form>
            @endif
          </div>
        </td>
      </tr>
      @endforeach
    </table>

    <form method="post" action="{{ route('accounts.save') }}" autocomplete="off" style="margin-top:14px; border-top:1px solid #222d45; padding-top:12px">
      @csrf
      <!-- Скрытые поля-ловушки, чтобы браузер никогда не подставлял сохранённый пароль/пинкод в API Secret -->
      <input type="text" name="_fake_user" autocomplete="username" tabindex="-1" style="position:absolute; opacity:0; height:0; width:0; pointer-events:none">
      <input type="password" name="_fake_pass" autocomplete="current-password" tabindex="-1" style="position:absolute; opacity:0; height:0; width:0; pointer-events:none">

      <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:4px">
        <h3 style="font-size:13px; color:#e2e8f0; margin:0">Добавить новый счёт или изменить существующий (по ID)</h3>
        <button type="button" class="btn-sm" id="btnResetForm" style="font-size:11px">Очистить форму для нового счёта</button>
      </div>
      <div class="form-grid">
        <label>ID счёта (латиница, напр. bot2)
          <input type="text" name="id" id="f_id" placeholder="bot2" autocomplete="off" required>
        </label>
        <label>Название счёта
          <input type="text" name="name" id="f_name" placeholder="Второй счёт (3x)" autocomplete="off" required>
        </label>
        <label>Биржа
          <select name="exchange" id="f_exchange">
            <option value="bybit">Bybit (Unified Futures)</option>
            <option value="binance">Binance Futures (каркас)</option>
            <option value="okx">OKX Swap (каркас)</option>
          </select>
        </label>
        <label>Стратегия
          <select name="strategy" id="f_strategy">
            <option value="schwartz_7x_sleeve_3x">Шварц + Спящая корзина</option>
            <option value="schwartz_only">Только Чемпионат Шварца</option>
            <option value="sleeve_only">Только Корзина (Carry/Momentum)</option>
          </select>
        </label>
        <label>Плечо Шварца (x)
          <input type="number" step="0.5" min="1" max="50" name="leverage" id="f_leverage" value="7" required>
        </label>
        <label>Плечо корзины (x)
          <input type="number" step="0.5" min="1" max="20" name="sleeve_leverage" id="f_sleeve_leverage" value="3" required>
        </label>
        <label>Риск на сделку (%)
          <input type="number" step="0.25" min="0.5" max="30" name="risk_pct" id="f_risk_pct" value="7.5" required>
        </label>
        <label>Макс. доля маржи (%)
          <input type="number" step="5" min="10" max="95" name="max_margin_frac" id="f_max_margin_frac" value="60" required>
        </label>
        <label>Макс. позиций одновременно
          <input type="number" step="1" min="1" max="6" name="max_conc" id="f_max_conc" value="2" required>
        </label>
        <label>Индивидуальный ПИН счёта
          <input type="text" name="pin" id="f_pin" placeholder="Напр. 223344" maxlength="12" autocomplete="off" required>
        </label>
        <label>API Key (оставьте пустым, чтобы не менять)
          <input type="text" name="api_key" id="f_api_key" value="" placeholder="Новый Bybit API Key" autocomplete="off" data-lpignore="true" readonly onfocus="this.removeAttribute('readonly');">
        </label>
        <label>API Secret (оставьте пустым, чтобы не менять)
          <input type="text" name="api_secret" id="f_api_secret" class="masked-secret" value="" placeholder="Новый Bybit API Secret" autocomplete="off" data-lpignore="true" readonly onfocus="this.removeAttribute('readonly');">
        </label>
      </div>
      <div style="display:flex; justify-content:space-between; align-items:center; margin-top:12px; flex-wrap:wrap; gap:12px">
        <div style="display:flex; gap:18px; align-items:center; flex-wrap:wrap">
          <label style="display:flex; align-items:center; gap:6px; font-size:12px; color:#e2e8f0; cursor:pointer">
            <input type="checkbox" name="enabled" id="f_enabled" value="1" checked> Аккаунт включён в планировщике
          </label>
          <label style="display:flex; align-items:center; gap:6px; font-size:12px; color:#fbbf24; cursor:pointer">
            <input type="checkbox" name="pause_new_trades" id="f_pause_new_trades" value="1"> ⏸ Не открывать новых сделок (только закрытие текущих)
          </label>
        </div>
        <button type="submit" class="btn-sm" style="padding:7px 16px; font-size:12.5px; background:#10b981; color:#052e16; border-color:#10b981; font-weight:700">Сохранить в S3 конфиг</button>
      </div>
    </form>
  </div>
</div>
@endif

<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<script>
var chartData = @json($chartData);
var periodStats = @json($periodStats);
var tradePaths = @json($tradePaths);
var accountName = @json($activeAcc['name'] ?? 'Bybit');
var isCalculating = @json($calculatingNow);
var currentPeriod = 'Квартал';
var currentMode = 'real'; // 'real' | 'bt'
var chart = null;

function clearApiInputs() {
  var k = document.getElementById('f_api_key');
  var s = document.getElementById('f_api_secret');
  if (k) k.value = '';
  if (s) s.value = '';
}
setTimeout(clearApiInputs, 50);
setTimeout(clearApiInputs, 400);

function renderMain() {
  var canvas = document.getElementById('mainChart');
  if (!canvas) return;
  var d = chartData[currentPeriod] || {real_labels:[], real_pnl:[], real_bal:[], bt_labels:[], bt_pnl:[], bt_eq:[]};
  var ctx = canvas.getContext('2d');
  if (chart) chart.destroy();

  var isReal = (currentMode === 'real');
  var labels = isReal ? d.real_labels : d.bt_labels;
  var values = isReal ? d.real_bal : d.bt_pnl;
  var color = isReal ? '#10b981' : '#f59e0b';
  var bg = isReal ? 'rgba(16,185,129,0.12)' : 'rgba(245,158,11,0.12)';

  document.getElementById('chartTitle').textContent = isReal
    ? ('Реальный баланс счёта (' + accountName + ') — ' + currentPeriod)
    : ('Эталонная кривая стратегии (% к $2,000) — ' + currentPeriod);

  chart = new Chart(ctx, {
    type: 'line',
    data: {
      labels: labels,
      datasets: [{
        label: isReal ? 'Баланс счёта ($)' : 'Доходность бэктеста (%)',
        data: values,
        borderColor: color,
        backgroundColor: bg,
        fill: true,
        borderWidth: 2,
        pointRadius: isReal ? 2.5 : 0,
        tension: 0.15
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: { labels: { color: '#e2e8f0' } },
        tooltip: {
          callbacks: {
            label: function(c) {
              var i = c.dataIndex;
              if (isReal) {
                var pct = (d.real_pnl && d.real_pnl[i] !== undefined) ? d.real_pnl[i] : 0;
                var sign = pct >= 0 ? '+' : '';
                return 'Баланс: $' + Number(c.parsed.y).toLocaleString('en-US', {minimumFractionDigits: 2}) + ' (' + sign + pct + '% TWR)';
              } else {
                var eq = (d.bt_eq && d.bt_eq[i]) ? d.bt_eq[i] : 2000;
                var sign2 = c.parsed.y >= 0 ? '+' : '';
                return 'Бэктест: ' + sign2 + c.parsed.y + '%  ($' + eq.toLocaleString('en-US', {minimumFractionDigits: 2}) + ')';
              }
            }
          }
        }
      },
      scales: {
        x: { ticks: { color: '#64748b', maxTicksLimit: 8 }, grid: { color: '#1e293b' } },
        y: {
          ticks: {
            color: '#64748b',
            callback: function(v) { return isReal ? ('$' + v) : (v + '%'); }
          },
          grid: { color: '#1e293b' }
        }
      }
    }
  });
}

function renderTrade(tr) {
  var id = tr.getAttribute('data-id');
  var coin = tr.getAttribute('data-coin');
  var side = parseInt(tr.getAttribute('data-side'), 10);
  var entry = parseFloat(tr.getAttribute('data-entry'));
  var exit = parseFloat(tr.getAttribute('data-exit'));
  var t0 = tr.getAttribute('data-time');
  var t1 = tr.getAttribute('data-exittime');
  var pnl = parseFloat(tr.getAttribute('data-pnl'));
  var pts = tradePaths[id];
  if (!pts || !pts.length) pts = [entry, (entry + exit) / 2, exit];
  var labels = pts.map(function(_, i) {
    if (i === 2) return 'ВХОД (' + t0.slice(5) + ')';
    if (i === pts.length - 3) return 'ВЫХОД (' + t1.slice(5) + ')';
    return (i - 2) + 'ч';
  });
  var entryLine = pts.map(function() { return entry; });
  var exitLine = pts.map(function() { return exit; });
  var ctx = document.getElementById('mainChart').getContext('2d');
  if (chart) chart.destroy();
  var win = pnl >= 0;
  chart = new Chart(ctx, {
    type: 'line',
    data: {
      labels: labels,
      datasets: [
        {
          label: coin + ' ' + (side === 1 ? 'LONG' : 'SHORT') + ' цена',
          data: pts,
          borderColor: win ? '#10b981' : '#ef4444',
          backgroundColor: win ? 'rgba(16,185,129,0.14)' : 'rgba(239,68,68,0.14)',
          fill: true,
          borderWidth: 2.2,
          pointRadius: 2,
          tension: 0.2
        },
        {
          label: 'Вход ' + entry,
          data: entryLine,
          borderColor: '#38bdf8',
          borderDash: [5, 5],
          borderWidth: 1.3,
          pointRadius: 0,
          fill: false
        },
        {
          label: 'Выход ' + exit,
          data: exitLine,
          borderColor: win ? '#10b981' : '#ef4444',
          borderDash: [3, 3],
          borderWidth: 1.3,
          pointRadius: 0,
          fill: false
        }
      ]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { labels: { color: '#e2e8f0' } } },
      scales: {
        x: { ticks: { color: '#64748b', maxTicksLimit: 8 }, grid: { color: '#1e293b' } },
        y: { ticks: { color: '#64748b' }, grid: { color: '#1e293b' } }
      }
    }
  });
  document.getElementById('chartTitle').textContent =
    coin + ' ' + (side === 1 ? 'LONG' : 'SHORT') + ' · вход ' + entry + ' → выход ' + exit + ' (' + (pnl >= 0 ? '+' : '') + pnl.toFixed(2) + '%)';
  document.getElementById('btnClose').style.display = 'inline-block';
}

function updateKpis() {
  var st = periodStats[currentPeriod];
  if (!st) return;
  document.querySelectorAll('.periodName').forEach(function(el){ el.textContent = currentPeriod; });

  document.getElementById('realStartBal').textContent = '$' + Number(st.real_start).toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2});
  var trSign = st.real_transfers_usd >= 0 ? '+$' : '-$';
  var trEl = document.getElementById('realTransfersUsd');
  if (trEl) {
    trEl.textContent = trSign + Math.abs(Number(st.real_transfers_usd || 0)).toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2});
    trEl.className = 'v ' + (st.real_transfers_usd >= 0 ? 'info' : 'warn');
  }

  var pSign = st.real_pnl_usd >= 0 ? '+$' : '-$';
  var rSign = st.real_ret >= 0 ? '+' : '';
  var pnlEl = document.getElementById('realPnlUsd');
  pnlEl.innerHTML = pSign + Math.abs(Number(st.real_pnl_usd)).toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2}) +
    ' (<span id="realRetPct">' + rSign + Number(st.real_ret).toFixed(2) + '%</span>)';
  pnlEl.className = 'v ' + (st.real_pnl_usd >= 0 ? 'ok' : 'bad');
  document.getElementById('realWins').textContent = st.real_wins;
  document.getElementById('realTrades').textContent = st.real_trades;
  document.getElementById('realMatched').textContent = st.matched;

  var btEl = document.getElementById('btRetPct');
  btEl.textContent = (st.bt_ret >= 0 ? '+' : '') + Number(st.bt_ret).toFixed(2) + '%';
  btEl.className = 'v ' + (st.bt_ret >= 0 ? 'ok' : 'bad');
  document.getElementById('btFinalEq').textContent = '$' + Number(st.bt_final).toLocaleString('en-US', {maximumFractionDigits: 0});
  document.getElementById('btWins').textContent = st.bt_wins;
  document.getElementById('btTotal').textContent = st.bt_total;
  document.getElementById('btDdPct').textContent = Number(st.bt_dd).toFixed(1) + '%';
}

function syncTables() {
  var rSec = document.getElementById('realTableSection');
  var bSec = document.getElementById('btTableSection');
  if (rSec) rSec.style.display = (currentMode === 'real') ? '' : 'none';
  if (bSec) bSec.style.display = (currentMode === 'bt') ? '' : 'none';
  document.querySelectorAll('.real-period-table').forEach(function(el) {
    el.style.display = (el.getAttribute('data-period') === currentPeriod) ? '' : 'none';
  });
  document.querySelectorAll('.bt-period-table').forEach(function(el) {
    el.style.display = (el.getAttribute('data-period') === currentPeriod) ? '' : 'none';
  });
}

document.getElementById('modeRealBtn').addEventListener('click', function() {
  currentMode = 'real';
  this.classList.add('active-real');
  document.getElementById('modeBtBtn').classList.remove('active-bt');
  var bc = document.getElementById('btnClose');
  if (bc) bc.style.display = 'none';
  syncTables();
  renderMain();
});

document.getElementById('modeBtBtn').addEventListener('click', function() {
  currentMode = 'bt';
  this.classList.add('active-bt');
  document.getElementById('modeRealBtn').classList.remove('active-real');
  var bc = document.getElementById('btnClose');
  if (bc) bc.style.display = 'none';
  syncTables();
  renderMain();
});

document.querySelectorAll('#periodTabs .tab').forEach(function(tab) {
  tab.addEventListener('click', function() {
    currentPeriod = tab.getAttribute('data-period');
    document.querySelectorAll('#periodTabs .tab').forEach(function(t){ t.classList.remove('active'); });
    tab.classList.add('active');
    var bc = document.getElementById('btnClose');
    if (bc) bc.style.display = 'none';
    updateKpis();
    syncTables();
    renderMain();
  });
});

var btnCloseEl = document.getElementById('btnClose');
if (btnCloseEl) {
  btnCloseEl.addEventListener('click', function() {
    this.style.display = 'none';
    renderMain();
  });
}

document.querySelectorAll('tr.trade-row').forEach(function(tr) {
  tr.addEventListener('click', function() {
    renderTrade(tr);
  });
});

var modalEl = document.getElementById('accountsModal');
var openBtn = document.getElementById('btnOpenAccounts');
var closeBtn = document.getElementById('btnCloseAccounts');
var resetBtn = document.getElementById('btnResetForm');
if (openBtn && modalEl) {
  openBtn.addEventListener('click', function(){
    clearApiInputs();
    modalEl.classList.add('open');
  });
}
if (closeBtn && modalEl) {
  closeBtn.addEventListener('click', function(){ modalEl.classList.remove('open'); });
}
if (resetBtn) {
  resetBtn.addEventListener('click', function(){
    document.getElementById('f_id').value = '';
    document.getElementById('f_name').value = '';
    document.getElementById('f_exchange').value = 'bybit';
    document.getElementById('f_strategy').value = 'schwartz_7x_sleeve_3x';
    document.getElementById('f_leverage').value = '7';
    document.getElementById('f_sleeve_leverage').value = '3';
    document.getElementById('f_risk_pct').value = '7.5';
    document.getElementById('f_max_margin_frac').value = '60';
    document.getElementById('f_max_conc').value = '2';
    document.getElementById('f_pin').value = '';
    document.getElementById('f_enabled').checked = true;
    var pEl = document.getElementById('f_pause_new_trades');
    if (pEl) pEl.checked = false;
    clearApiInputs();
  });
}
document.querySelectorAll('.btn-edit-acc').forEach(function(btn) {
  btn.addEventListener('click', function() {
    document.getElementById('f_id').value = btn.getAttribute('data-id') || '';
    document.getElementById('f_name').value = btn.getAttribute('data-name') || '';
    document.getElementById('f_exchange').value = btn.getAttribute('data-exchange') || 'bybit';
    document.getElementById('f_strategy').value = btn.getAttribute('data-strategy') || 'schwartz_7x_sleeve_3x';
    document.getElementById('f_leverage').value = btn.getAttribute('data-leverage') || '7';
    document.getElementById('f_sleeve_leverage').value = btn.getAttribute('data-sleeve_leverage') || '3';
    document.getElementById('f_risk_pct').value = btn.getAttribute('data-risk_pct') || '7.5';
    document.getElementById('f_max_margin_frac').value = btn.getAttribute('data-max_margin_frac') || '60';
    document.getElementById('f_max_conc').value = btn.getAttribute('data-max_conc') || '2';
    document.getElementById('f_pin').value = btn.getAttribute('data-pin') || '';
    document.getElementById('f_enabled').checked = btn.getAttribute('data-enabled') === '1';
    var pEl = document.getElementById('f_pause_new_trades');
    if (pEl) pEl.checked = btn.getAttribute('data-pause_new_trades') === '1';
    clearApiInputs();
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
setTimeout(function(){ if (!modalEl || !modalEl.classList.contains('open')) location.reload(); }, isCalculating ? 15000 : 60000);

updateKpis();
syncTables();
renderMain();
</script>
@endsection
