@extends('layouts.app')

@section('title', 'Как работает бот')

@section('content')
<style>
    .container { max-width: 980px; }
    .hero { padding: 12px 0 8px; }
    .hero h1 { font-size: 34px; line-height: 1.15; letter-spacing: -0.03em; margin-bottom: 10px; }
    .hero p { color: var(--muted); max-width: 640px; }
    .facts { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin: 22px 0; }
    .fact { background: var(--card); border: 1px solid var(--border); border-radius: 10px; padding: 14px 16px; }
    .fact b { display: block; font-size: 22px; margin-top: 4px; }
    .parts { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin: 8px 0 28px; }
    .part { background: var(--card); border: 1px solid var(--border); border-radius: 10px; padding: 16px 18px; }
    .part h2 { font-size: 16px; margin-bottom: 8px; }
    .part p, .case p { color: var(--muted); font-size: 14px; }
    .calc { background: var(--card); border: 1px solid var(--border); border-radius: 10px; padding: 18px; margin-bottom: 28px; }
    .calc form { display: flex; gap: 12px; flex-wrap: wrap; align-items: end; }
    .calc label { display: flex; flex-direction: column; gap: 6px; font-size: 12px; color: var(--muted); }
    .calc input { width: 180px; }
    .result { display: flex; gap: 28px; flex-wrap: wrap; margin: 16px 0 8px; }
    .result b { font-size: 28px; letter-spacing: -0.03em; }
    canvas { width: 100%; height: 280px; display: block; }
    .case { display: grid; grid-template-columns: 1.3fr 0.7fr; gap: 16px; background: var(--card); border: 1px solid var(--border); border-radius: 10px; padding: 16px 18px; margin-bottom: 12px; }
    .meta { font-size: 13px; color: var(--muted); margin-bottom: 8px; }
    .note { color: var(--muted); font-size: 13px; }
    @media (max-width: 800px) {
        .facts, .parts, .case { grid-template-columns: 1fr; }
        .hero h1 { font-size: 28px; }
    }
</style>

<div class="hero">
    <h1>Один счёт. Правила входа и выхода записаны заранее.</h1>
    <p>Ниже не прогноз. Это тот же бэктест, что на странице сверки: старт $2&nbsp;000, комиссия и фандинг уже внутри кривой. Калькулятор только переносит эту кривую на другую сумму и дату.</p>
    <p style="margin-top:12px"><a class="btn btn-outline" href="{{ route('home') }}">К сверке счёта</a></p>
</div>

@if (!$story['ready'])
    <div class="card"><p>Бэктест ещё не записан. Страница появится после расчёта кривой.</p></div>
@else
    <div class="facts">
        <div class="fact"><span class="stat-label">Доходность всей истории</span><b class="pos">{{ $story['stats']['return_pct'] >= 0 ? '+' : '' }}{{ number_format($story['stats']['return_pct'], 0, '.', ' ') }}%</b></div>
        <div class="fact"><span class="stat-label">Итог с $2 000</span><b>${{ number_format($story['stats']['final'], 0, '.', ' ') }}</b></div>
        <div class="fact"><span class="stat-label">Максимальная просадка</span><b class="neg">{{ number_format($story['stats']['max_dd_pct'], 1, '.', ' ') }}%</b></div>
        <div class="fact"><span class="stat-label">Сделки</span><b>{{ $story['stats']['schwartz'] }} + {{ $story['stats']['legs'] }}</b><span class="note">Шварц и ноги корзины</span></div>
    </div>
    <p class="note" style="margin-top:-12px; margin-bottom:22px">Период кривой: {{ $story['stats']['from'] }} — {{ $story['stats']['to'] }}, UTC. Просадка {{ number_format($story['stats']['max_dd_pct'], 1, '.', ' ') }}% на этой истории была реальной: счёт от пика падал на эту долю.</p>

    @if ($story['windows'])
    <div class="facts">
        @foreach ($story['windows'] as $w)
        <div class="fact">
            <span class="stat-label">{{ $w['label'] }}</span>
            <b class="{{ ($w['return_pct'] ?? 0) >= 0 ? 'pos' : 'neg' }}">{{ ($w['return_pct'] ?? 0) >= 0 ? '+' : '' }}{{ number_format($w['return_pct'], 1, '.', ' ') }}%</b>
            <span class="note">просадка {{ $w['max_dd_pct'] !== null ? number_format($w['max_dd_pct'], 1, '.', ' ') : '—' }}%</span>
        </div>
        @endforeach
    </div>
    @endif

    <h2>Из чего состоит бот</h2>
    <div class="parts">
        @foreach ($story['parts'] as $part)
        <div class="part">
            <h2>{{ $part['title'] }}</h2>
            <p>{{ $part['text'] }}</p>
        </div>
        @endforeach
    </div>

    <div class="calc">
        <h2>Если вложить свою сумму</h2>
        <p class="note" style="margin-bottom:14px">Берётся фактическая эквити бэктеста. Итог = ваша сумма × эквити в конце периода ÷ эквити в выбранный день. Будущие дни не достраиваются.</p>
        <form id="calc" onsubmit="return false">
            <label>Сумма, USDT
                <input id="amount" type="number" min="100" step="100" value="1000">
            </label>
            <label>Дата входа
                <input id="when" type="date" value="{{ $story['stats']['from'] }}" min="{{ $story['stats']['from'] }}" max="{{ $story['stats']['to'] }}">
            </label>
        </form>
        <div class="result">
            <div><span class="stat-label">Банк на {{ $story['stats']['to'] }}</span><b id="bank">—</b></div>
            <div><span class="stat-label">Доходность от даты</span><b id="ret">—</b></div>
            <div><span class="stat-label">Просадка в этом окне</span><b id="dd" class="neg">—</b></div>
        </div>
        <canvas id="chart" width="900" height="280"></canvas>
        <p class="note" id="calcNote"></p>
    </div>

    <h2>Как это выглядело на конкретных сделках</h2>
    <p class="note" style="margin-bottom:12px">Сделки выбраны из того же файла бэктеста. Доллар сделки — это уже выросший счёт, а не результат вклада в $1&nbsp;000. Вклад в эквити на момент входа указан отдельно.</p>
    @foreach ($story['cases'] as $case)
    <article class="case">
        <div>
            <div class="meta">{{ $case['time'] }} → {{ $case['exit_time'] }} UTC · {{ $case['coin'] }} · {{ $case['side'] }} · {{ $case['hold_h'] }} ч · {{ $case['reason'] }}@if($case['pyr']) · с пирамидой@endif</div>
            <h2>{{ $case['title'] }}</h2>
            <p>{{ $case['why_in'] }}</p>
            <p style="margin-top:8px">{{ $case['why_out'] }} Цена {{ $case['entry'] }} → {{ $case['exit'] }}.</p>
            <p style="margin-top:8px">Результат на том счёте: <b class="{{ ($case['pnl_usd'] ?? 0) >= 0 ? 'pos' : 'neg' }}">{{ ($case['pnl_usd'] ?? 0) >= 0 ? '+' : '−' }}${{ number_format(abs($case['pnl_usd'] ?? 0), 0, '.', ' ') }}</b>, вклад в эквити {{ ($case['pnl_pct'] ?? 0) >= 0 ? '+' : '' }}{{ number_format($case['pnl_pct'] ?? 0, 2, '.', ' ') }}%. Эквити после сделки ${{ number_format($case['eq'] ?? 0, 0, '.', ' ') }}.</p>
        </div>
        <canvas class="spark" data-up="{{ ($case['pnl_usd'] ?? 0) >= 0 ? '1' : '0' }}" data-path="{{ json_encode($case['path']) }}" width="320" height="140"></canvas>
    </article>
    @endforeach
@endif

<script>
const curve = @json($story['curve']);
const amount = document.getElementById('amount');
const when = document.getElementById('when');
const canvas = document.getElementById('chart');

function draw(points) {
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    const w = canvas.width, h = canvas.height;
    ctx.clearRect(0, 0, w, h);
    if (points.length < 2) return;
    const vals = points.map(p => p.v);
    const min = Math.min(...vals), max = Math.max(...vals);
    const span = Math.max(max - min, 1);
    ctx.strokeStyle = '#3b82f6';
    ctx.lineWidth = 2;
    ctx.beginPath();
    points.forEach((p, i) => {
        const x = 8 + (w - 16) * (i / (points.length - 1));
        const y = h - 16 - ((p.v - min) / span) * (h - 32);
        if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    });
    ctx.stroke();
    ctx.fillStyle = '#94a3b8';
    ctx.font = '12px sans-serif';
    ctx.fillText(points[0].d, 8, h - 4);
    const end = points[points.length - 1].d;
    ctx.fillText(end, w - ctx.measureText(end).width - 8, h - 4);
}

function run() {
    if (!curve.length || !amount) return;
    const money = Math.max(0, Number(amount.value) || 0);
    const day = when.value;
    let i = curve.findIndex(p => p.d >= day);
    if (i < 0) i = curve.length - 1;
    const base = curve[i].eq;
    const last = curve[curve.length - 1].eq;
    const sliced = curve.slice(i).map(p => ({ d: p.d, v: money * p.eq / base }));
    let peak = sliced[0].v, dd = 0;
    sliced.forEach(p => { peak = Math.max(peak, p.v); if (peak > 0) dd = Math.max(dd, (peak - p.v) / peak); });
    const bank = money * last / base;
    const ret = (last / base - 1) * 100;
    document.getElementById('bank').textContent = '$' + Math.round(bank).toLocaleString('ru-RU');
    document.getElementById('bank').className = ret >= 0 ? 'pos' : 'neg';
    document.getElementById('ret').textContent = (ret >= 0 ? '+' : '') + ret.toFixed(1) + '%';
    document.getElementById('ret').className = ret >= 0 ? 'pos' : 'neg';
    document.getElementById('dd').textContent = (dd * 100).toFixed(1) + '%';
    document.getElementById('calcNote').textContent = 'Окно ' + curve[i].d + ' — ' + curve[curve.length - 1].d + '. В бэктесте в этот день счёт был $' + Math.round(base).toLocaleString('ru-RU') + ' при старте $2 000.';
    const step = Math.max(1, Math.floor(sliced.length / 240));
    const shown = sliced.filter((_, n) => n % step === 0);
    if (shown[shown.length - 1] !== sliced[sliced.length - 1]) shown.push(sliced[sliced.length - 1]);
    draw(shown);
}
if (amount) {
    amount.addEventListener('input', run);
    when.addEventListener('input', run);
    run();
}
document.querySelectorAll('.spark').forEach(el => {
    const path = JSON.parse(el.dataset.path || '[]');
    const ctx = el.getContext('2d');
    const w = el.width, h = el.height;
    ctx.clearRect(0, 0, w, h);
    if (path.length < 2) return;
    const min = Math.min(...path), max = Math.max(...path);
    const span = Math.max(max - min, 1e-9);
    ctx.strokeStyle = el.dataset.up === '1' ? '#10b981' : '#ef4444';
    ctx.lineWidth = 2;
    ctx.beginPath();
    path.forEach((v, i) => {
        const x = (w - 4) * (i / (path.length - 1)) + 2;
        const y = h - 8 - ((v - min) / span) * (h - 16);
        if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    });
    ctx.stroke();
});
</script>
@endsection
