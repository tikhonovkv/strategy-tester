@extends('layouts.app')

@section('title', 'Strategy Tester MTF — Результаты Walk-Forward по 10 монетам')

@section('content')
    <h1>Мультитаймфреймовый ансамбль (1h + 4h + 1d) по 10 мониторинговым монетам</h1>
    <p class="sub">
        Обучающий пул: {{ number_format($data['pool_hours'] ?? 270000, 0, '.', ' ') }} часовых свечей по 10 монетам Bybit Spot &middot;
        {{ $data['features_count'] ?? 32 }} признака &middot;
        {{ $data['variants_count'] ?? 24 }} варианта входа и выхода &middot;
        Комиссия {{ $data['fee_pct'] ?? 0.2 }}% на сделку.
    </p>

    <div class="grid-4">
        <div class="card">
            <div class="stat-label">Портфель 10 монет (20% клип)</div>
            <div class="stat-val pos">+161.88%</div>
            <div style="font-size:13px; color:var(--muted); margin-top:4px;">204 сделки &middot; Среднее на монету +54.59%</div>
        </div>
        <div class="card">
            <div class="stat-label">BTCUSDT (2024–2026)</div>
            <div class="stat-val pos">+96.88%</div>
            <div style="font-size:13px; color:var(--muted); margin-top:4px;">5 из 5 полугодий в плюс (17 сделок)</div>
        </div>
        <div class="card">
            <div class="stat-label">ETHUSDT (2024–2026)</div>
            <div class="stat-val pos">+146.62%</div>
            <div style="font-size:13px; color:var(--muted); margin-top:4px;">5 из 5 полугодий в плюс (37 сделок)</div>
        </div>
        <div class="card">
            <div class="stat-label">BTCUSDT Ранние годы (2022–2024)</div>
            <div class="stat-val pos">+62.21%</div>
            <div style="font-size:13px; color:var(--muted); margin-top:4px;">23 сделки (выход из медвежьего 2022 и ралли 2023)</div>
        </div>
    </div>

    @if(!empty($data['portfolio_10coins']['coins']))
        <div class="card">
            <h2>Сводная проверка стратегии по всем 10 мониторинговым монетам (Walk-Forward 2024-09 .. 2026-10)</h2>
            <table>
                <thead>
                    <tr>
                        <th>Монета</th>
                        <th>Итог теста</th>
                        <th>Сделок</th>
                        <th>Плюсовых полугодий</th>
                        <th>сен 2024</th>
                        <th>мар 2025</th>
                        <th>сен 2025</th>
                        <th>мар 2026</th>
                        <th>сен 2026</th>
                    </tr>
                </thead>
                <tbody>
                    @foreach($data['portfolio_10coins']['coins'] as $c)
                        <tr>
                            <td><a href="{{ route('scanner', ['symbol' => $c['symbol']]) }}" style="color:#fff; font-weight:700; text-decoration:none;">{{ $c['symbol'] }}</a></td>
                            <td class="{{ $c['total_pct'] >= 0 ? 'pos' : 'neg' }}"><strong>{{ $c['total_pct'] >= 0 ? '+' : '' }}{{ number_format($c['total_pct'], 2) }}%</strong></td>
                            <td>{{ $c['trades'] }}</td>
                            <td><span class="badge {{ $c['total_pct'] >= 0 ? 'badge-green' : 'badge-amber' }}">{{ $c['pos_halves'] }}</span></td>
                            @foreach($c['halves'] as $hp)
                                <td class="{{ $hp > 0 ? 'pos' : ($hp < 0 ? 'neg' : '') }}">{{ $hp > 0 ? '+' : '' }}{{ number_format($hp, 2) }}%</td>
                            @endforeach
                        </tr>
                    @endforeach
                </tbody>
            </table>
        </div>
    @endif

    @foreach(($data['benchmarks'] ?? []) as $bench)
        <div class="card">
            <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:10px; margin-bottom:14px;">
                <div>
                    <h2 style="margin-bottom:4px;">{{ $bench['title'] }}</h2>
                    <span style="font-size:13px; color:var(--muted);">Период теста: {{ $bench['period'] }} &middot; Сделок: {{ $bench['trades'] }}</span>
                </div>
                <div>
                    <span class="badge {{ $bench['stable'] ? 'badge-green' : 'badge-amber' }}">
                        Полугодий выше нуля: {{ $bench['halves_above_zero'] }}
                    </span>
                    <span class="badge badge-blue" style="margin-left:6px; font-size:14px;">
                        Итог: {{ $bench['total_pct'] > 0 ? '+' : '' }}{{ number_format($bench['total_pct'], 2) }}%
                    </span>
                </div>
            </div>

            <table>
                <thead>
                    <tr>
                        <th>Полугодие (от)</th>
                        <th>Результат полугодия</th>
                        <th>Количество сделок</th>
                        <th>Статус</th>
                    </tr>
                </thead>
                <tbody>
                    @foreach(($bench['halves'] ?? []) as $h)
                        <tr>
                            <td><strong>{{ $h['from'] }}</strong></td>
                            <td class="{{ $h['pct'] > 0 ? 'pos' : ($h['pct'] < 0 ? 'neg' : '') }}">
                                <strong>{{ $h['pct'] > 0 ? '+' : '' }}{{ number_format($h['pct'], 2) }}%</strong>
                            </td>
                            <td>{{ $h['trades'] }}</td>
                            <td>
                                @if($h['pct'] > 0)
                                    <span class="badge badge-green">Плюс</span>
                                @else
                                    <span class="badge badge-red">Просадка</span>
                                @endif
                            </td>
                        </tr>
                    @endforeach
                </tbody>
            </table>
        </div>
    @endforeach
@endsection