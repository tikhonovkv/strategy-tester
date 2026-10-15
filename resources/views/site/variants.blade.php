@extends('layouts.app')

@section('title', 'Матрица 24 вариантов — Strategy Tester MTF')

@section('content')
    <h1>Матрица вариантов входа и выхода на каждом часе</h1>
    <p class="sub">
        Двухголовая модель (HistGradientBoostingRegressor + HistGradientBoostingClassifier с L2-регуляризацией) оценивает каждый вариант по 32 мультитаймфреймовым признакам и ранжирует их по стандартизированному счёту <code>0.5 * (z_reg + z_clf)</code>.
    </p>

    <div class="card">
        <table>
            <thead>
                <tr>
                    <th>#</th>
                    <th>Код варианта</th>
                    <th>Сторона</th>
                    <th>Эшелон</th>
                    <th>Точка входа</th>
                    <th>Стоп-лосс</th>
                    <th>Выход</th>
                    <th>Corr (OOS)</th>
                    <th>Mean Top-15%</th>
                </tr>
            </thead>
            <tbody>
                @foreach($variants as $v)
                    <tr>
                        <td>{{ $v['id'] }}</td>
                        <td><code>{{ $v['code'] }}</code></td>
                        <td>
                            <span class="badge {{ $v['side'] === 'LONG' ? 'badge-green' : 'badge-red' }}">
                                {{ $v['side'] }}
                            </span>
                        </td>
                        <td>{{ $v['echelon'] }}</td>
                        <td>{{ $v['entry'] }}</td>
                        <td>{{ $v['stop'] }}</td>
                        <td>{{ $v['exit'] }}</td>
                        <td class="pos"><strong>{{ $v['corr'] }}</strong></td>
                        <td class="pos"><strong>{{ $v['top_mean'] }}</strong></td>
                    </tr>
                @endforeach
            </tbody>
        </table>
    </div>
@endsection
