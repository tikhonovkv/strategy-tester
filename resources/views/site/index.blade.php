@extends('layouts.app')

@section('title', 'Strategy Tester MTF — Результаты Walk-Forward тестирования')

@section('content')
    <h1>Мультитаймфреймовый ансамбль (1h + 4h + 1d) с защитой от перекупленности</h1>
    <p class="sub">
        Обучающий пул: {{ number_format($data['pool_hours'] ?? 70108, 0, '.', ' ') }} размеченных часов (BTC 2021–2026 + ETH 2023–2026) &middot;
        {{ $data['features_count'] ?? 32 }} признака &middot;
        {{ $data['variants_count'] ?? 24 }} варианта входа и выхода &middot;
        Честная комиссия {{ $data['fee_pct'] ?? 0.2 }}% на сделку по минутным свечам.
    </p>

    <div class="grid-4">
        <div class="card">
            <div class="stat-label">BTCUSDT (2024–2026, 3 рукава)</div>
            <div class="stat-val pos">+96.88%</div>
            <div style="font-size:13px; color:var(--muted); margin-top:4px;">5 из 5 полугодий в плюс (17 сделок)</div>
        </div>
        <div class="card">
            <div class="stat-label">ETHUSDT (2024–2026, 2 слота / 24 вар.)</div>
            <div class="stat-val pos">+89.80%</div>
            <div style="font-size:13px; color:var(--muted); margin-top:4px;">5 из 5 полугодий в плюс (33 сделки)</div>
        </div>
        <div class="card">
            <div class="stat-label">BTCUSDT (2024–2026, 2 слота / 24 вар.)</div>
            <div class="stat-val pos">+89.02%</div>
            <div style="font-size:13px; color:var(--muted); margin-top:4px;">5 из 5 полугодий в плюс (25 сделок)</div>
        </div>
        <div class="card">
            <div class="stat-label">BTCUSDT Ранние годы (2022–2024)</div>
            <div class="stat-val pos">+62.21%</div>
            <div style="font-size:13px; color:var(--muted); margin-top:4px;">23 сделки (выход из медвежьего 2022 и ралли 2023)</div>
        </div>
    </div>

    <div style="margin-bottom: 24px; display:flex; gap:12px; flex-wrap:wrap;">
        <a href="{{ route('scanner', ['symbol' => 'BTCUSDT']) }}" class="btn">Запустить живой сканер BTCUSDT</a>
        <a href="{{ route('scanner', ['symbol' => 'ETHUSDT']) }}" class="btn btn-outline">Сканировать ETHUSDT</a>
        <a href="{{ route('scanner', ['symbol' => 'SOLUSDT']) }}" class="btn btn-outline">Сканировать SOLUSDT</a>
    </div>

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
