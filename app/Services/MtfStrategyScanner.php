<?php

namespace App\Services;

use Illuminate\Support\Facades\Http;

class MtfStrategyScanner
{
    public const VARIANTS = [
        // Fast 4h-SMA18 long swings (0..3)
        ['id' => 0,  'code' => 'long_lim_low_sl24_h4sma18',     'side' => 'LONG',  'echelon' => '4h Swing',  'entry' => 'Лимит на low часа (ждёт 12ч)',   'stop' => 'Минимум 24ч (-0.8% буфер)', 'exit' => 'Закрытие 4h < 4h SMA18',            'corr' => '+0.082', 'top_mean' => '+1.12%'],
        ['id' => 1,  'code' => 'long_lim_03_sl24_h4sma18_tp4',  'side' => 'LONG',  'echelon' => '4h Swing',  'entry' => 'Лимит -0.3% от close (12ч)',    'stop' => 'Минимум 24ч / -1.5%',       'exit' => 'Тейк +4% или 4h < 4h SMA18',        'corr' => '+0.091', 'top_mean' => '+1.25%'],
        ['id' => 2,  'code' => 'long_lim_06_sl24_h4sma18_tp5',  'side' => 'LONG',  'echelon' => '4h Swing',  'entry' => 'Лимит -0.6% от close (12ч)',    'stop' => 'Минимум 24ч / -1.8%',       'exit' => 'Тейк +5% или 4h < 4h SMA18',        'corr' => '+0.095', 'top_mean' => '+1.38%'],
        ['id' => 3,  'code' => 'long_lim_10_sl48_h4sma18_tp6',  'side' => 'LONG',  'echelon' => '4h Swing',  'entry' => 'Лимит -1.0% от close (24ч)',    'stop' => 'Минимум 48ч / -2.2%',       'exit' => 'Тейк +6% или 4h < 4h SMA18',        'corr' => '+0.104', 'top_mean' => '+1.54%'],
        // Medium 1d-SMA20 long swings (4..7)
        ['id' => 4,  'code' => 'long_lim_low_sl24_day20',       'side' => 'LONG',  'echelon' => '1d SMA20',  'entry' => 'Лимит на low часа (ждёт 24ч)',   'stop' => 'Минимум 24ч',               'exit' => 'Закрытие дня < 1d SMA20',           'corr' => '+0.073', 'top_mean' => '+1.60%'],
        ['id' => 5,  'code' => 'long_lim_mid24_sl24_day20',     'side' => 'LONG',  'echelon' => '1d SMA20',  'entry' => 'Середина (hour_low + low_24)/2', 'stop' => 'Минимум 24ч',               'exit' => 'Закрытие дня < 1d SMA20',           'corr' => '+0.068', 'top_mean' => '+1.42%'],
        ['id' => 6,  'code' => 'long_lim_05_sl24_day20_tp8',    'side' => 'LONG',  'echelon' => '1d SMA20',  'entry' => 'Лимит -0.5% от close (24ч)',    'stop' => 'Минимум 24ч / -1.8%',       'exit' => 'Тейк +8% или 1d close < SMA20',     'corr' => '+0.107', 'top_mean' => '+1.84%'],
        ['id' => 7,  'code' => 'long_lim_10_sl48_day20_tp10',   'side' => 'LONG',  'echelon' => '1d SMA20',  'entry' => 'Лимит -1.0% от close (24ч)',    'stop' => 'Минимум 48ч / -2.5%',       'exit' => 'Тейк +10% или 1d close < SMA20',    'corr' => '+0.188', 'top_mean' => '+3.16%'],
        // Core 1d-SMA50 long trend rides (8..11)
        ['id' => 8,  'code' => 'long_lim_low_sl24_day50',       'side' => 'LONG',  'echelon' => '1d SMA50',  'entry' => 'Лимит на low часа (ждёт 24ч)',   'stop' => 'Минимум дня / 24ч',         'exit' => 'Закрытие дня < 1d SMA50',           'corr' => '+0.144', 'top_mean' => '+2.84%'],
        ['id' => 9,  'code' => 'long_lim_low_sl48_day50',       'side' => 'LONG',  'echelon' => '1d SMA50',  'entry' => 'Лимит на low часа (ждёт 24ч)',   'stop' => 'Минимум 2 дней / 48ч',      'exit' => 'Закрытие дня < 1d SMA50',           'corr' => '+0.123', 'top_mean' => '+3.80%'],
        ['id' => 10, 'code' => 'long_lim_05_sl24_day50',        'side' => 'LONG',  'echelon' => '1d SMA50',  'entry' => 'Лимит -0.5% от close (24ч)',    'stop' => 'Минимум 24ч / -1.8%',       'exit' => 'Закрытие дня < 1d SMA50',           'corr' => '+0.169', 'top_mean' => '+3.44%'],
        ['id' => 11, 'code' => 'long_lim_10_sl48_day50',        'side' => 'LONG',  'echelon' => '1d SMA50',  'entry' => 'Лимит -1.0% от close (24ч)',    'stop' => 'Минимум 48ч / -2.5%',       'exit' => 'Закрытие дня < 1d SMA50',           'corr' => '+0.123', 'top_mean' => '+2.88%'],
        // Bear short variants (12..17)
        ['id' => 12, 'code' => 'short_lim_high_sl24_h4sma18',   'side' => 'SHORT', 'echelon' => '4h Bear',   'entry' => 'Лимит на high часа (ждёт 12ч)',  'stop' => 'Максимум 24ч (+2.0% буфер)','exit' => 'Закрытие 4h > 4h SMA18',            'corr' => '+0.254', 'top_mean' => '+1.45%'],
        ['id' => 13, 'code' => 'short_lim_03_sl24_h4sma18_tp4', 'side' => 'SHORT', 'echelon' => '4h Bear',   'entry' => 'Лимит +0.3% от close (12ч)',    'stop' => 'Максимум 24ч (+2.0% буфер)','exit' => 'Тейк -4% или 4h > 4h SMA18',        'corr' => '+0.146', 'top_mean' => '+1.18%'],
        ['id' => 14, 'code' => 'short_lim_06_sl24_h4sma18_tp5', 'side' => 'SHORT', 'echelon' => '4h Bear',   'entry' => 'Лимит +0.6% от close (12ч)',    'stop' => 'Максимум 24ч (+2.5% буфер)','exit' => 'Тейк -5% или 4h > 4h SMA18',        'corr' => '+0.156', 'top_mean' => '+1.34%'],
        ['id' => 15, 'code' => 'short_lim_high_day50',          'side' => 'SHORT', 'echelon' => '1d Bear',   'entry' => 'Лимит на high часа (ждёт 24ч)',  'stop' => 'Максимум 24ч (+2.0% буфер)','exit' => 'Закрытие дня > 1d SMA50 (до 14д)',  'corr' => '+0.121', 'top_mean' => '+1.62%'],
        ['id' => 16, 'code' => 'short_lim_1pct_day50',          'side' => 'SHORT', 'echelon' => '1d Bear',   'entry' => 'Лимит +1.0% от close (24ч)',    'stop' => '+2.5% от входа',            'exit' => 'Закрытие дня > 1d SMA50 (до 14д)',  'corr' => '+0.119', 'top_mean' => '+1.75%'],
        ['id' => 17, 'code' => 'short_lim_2pct_day50',          'side' => 'SHORT', 'echelon' => '1d Bear',   'entry' => 'Лимит +2.0% от close (24ч)',    'stop' => '+3.0% от входа',            'exit' => 'Закрытие дня > 1d SMA50 (до 14д)',  'corr' => '+0.163', 'top_mean' => '+2.31%'],
    ];

    public function scanBybitSpot(string $symbol = 'BTCUSDT'): array
    {
        $symbol = strtoupper(trim($symbol));
        if (!preg_match('/^[A-Z0-9]{2,12}USDT$/', $symbol)) {
            $symbol = 'BTCUSDT';
        }

        try {
            $h1 = $this->fetchKlines($symbol, '60', 200);
            $h4 = $this->fetchKlines($symbol, '240', 100);
            $d1 = $this->fetchKlines($symbol, 'D', 90);
        } catch (\Throwable $e) {
            return [
                'ok' => false,
                'symbol' => $symbol,
                'error' => 'Не удалось получить свечи Bybit REST: ' . $e->getMessage(),
            ];
        }

        if (count($h1) < 80 || count($h4) < 35 || count($d1) < 60) {
            return [
                'ok' => false,
                'symbol' => $symbol,
                'error' => 'Недостаточно закрытых свечей для расчёта 50-дневной средней и мультитаймфреймового RSI.',
            ];
        }

        $h1Closes = array_column($h1, 'close');
        $h1Highs  = array_column($h1, 'high');
        $h1Lows   = array_column($h1, 'low');

        $h4Closes = array_column($h4, 'close');
        $d1Closes = array_column($d1, 'close');
        $d1Highs  = array_column($d1, 'high');
        $d1Lows   = array_column($d1, 'low');

        $lastH1 = end($h1);
        $price  = (float) $lastH1['close'];
        $barLow = (float) $lastH1['low'];
        $barHigh = (float) $lastH1['high'];
        $decimals = $price >= 100 ? 2 : ($price >= 1 ? 4 : 5);

        $hRsi14 = $this->rsi($h1Closes, 14);
        $hRsi7  = $this->rsi($h1Closes, 7);
        $hSma24 = $this->sma($h1Closes, 24);
        $hSma72 = $this->sma($h1Closes, 72);

        $h4Rsi14 = $this->rsi($h4Closes, 14);
        $h4Sma18 = $this->sma($h4Closes, 18);

        $dRsi14  = $this->rsi($d1Closes, 14);
        $dSma20  = $this->sma($d1Closes, 20);
        $dSma50  = $this->sma($d1Closes, 50);
        $dSma50Prev5 = $this->sma(array_slice($d1Closes, 0, -5), 50);

        $low24  = min(array_slice($h1Lows, -24));
        $high24 = max(array_slice($h1Highs, -24));
        $low48  = min(array_slice($h1Lows, -48));
        $high48 = max(array_slice($h1Highs, -48));

        $nDays = count($d1Closes);
        $lastDayClose = (float) $d1Closes[$nDays - 1];
        $lastDayLow   = (float) $d1Lows[$nDays - 1];
        $lastDayHigh  = (float) $d1Highs[$nDays - 1];
        $prevDayClose = (float) $d1Closes[$nDays - 2];
        $prevDayLow   = (float) $d1Lows[$nDays - 2];
        $prevDayHigh  = (float) $d1Highs[$nDays - 2];

        $slope5d = ($dSma50 - $dSma50Prev5) / max($lastDayClose, 1e-8);
        $dayDist50 = ($lastDayClose - $dSma50) / max($lastDayClose, 1e-8);
        $dayDist20 = ($lastDayClose - $dSma20) / max($lastDayClose, 1e-8);
        $h4Dist18  = ((float) end($h4Closes) - $h4Sma18) / max((float) end($h4Closes), 1e-8);
        $hourAlign = ($hSma24 - $hSma72) / max($price, 1e-8);

        $twoBull = ($lastDayClose > $dSma50) && ($lastDayLow > $dSma50) && ($prevDayClose > $dSma50) && ($prevDayLow > $dSma50);
        $twoBear = ($lastDayClose < $dSma50) && ($prevDayClose < $dSma50);

        $overboughtGuard = ($dRsi14 > 70.0) || ($h4Rsi14 > 72.0) || ($hRsi14 > 70.0);
        $oversoldGuard   = ($dRsi14 < 30.0) || ($h4Rsi14 < 30.0) || ($hRsi14 < 30.0);

        $regime = 'NEUTRAL_CASH';
        $regimeTitle = 'КЭШ (Вне рынка — ожидание выравнивания таймфреймов)';
        $recommended = null;

        if ($twoBull && $slope5d > 0) {
            if ($overboughtGuard) {
                $regime = 'OVERBOUGHT_COOLDOWN';
                $regimeTitle = 'ЗАЩИТА ОТ ПЕРЕКУПЛЕННОСТИ (RSI > 70 — покупка вершины заблокирована)';
            } elseif ($h4Dist18 > 0 && $hourAlign > 0 && $hRsi14 <= 68.0) {
                $regime = 'BULL_WAVE_ACTIVE';
                $regimeTitle = 'БЫЧИЙ ТРЕНД ПОДТВЕРЖДЁН (1d SMA50 + 4h SMA18 + 1h SMA24>72 без перекупленности)';
                $isPullbackDip = ($lastDayLow < $prevDayLow) && ($barLow > $prevDayLow);
                if ($isPullbackDip) {
                    $recommended = [
                        'variant' => 'long_hrlow_sl_2daylow_sma50 (Рукав 1: Точный дневной ретест)',
                        'side' => 'LONG',
                        'limit_price' => round($barLow, $decimals),
                        'stop_price' => round(min($lastDayLow, $prevDayLow), $decimals),
                        'target_price' => 'Удержание до закрытия дня < SMA50 (' . round($dSma50, $decimals) . ')',
                        'cancel_rule' => 'Отменить лимит, если 1h SMA24 уйдёт под SMA72 до касания цены',
                    ];
                } else {
                    $recommended = [
                        'variant' => 'long_lim_low_sl48_day50 / long_lim_05_sl24_day20_tp8 (Рукав 2: Свинг / Тренд)',
                        'side' => 'LONG',
                        'limit_price' => round(min($barLow, $price * 0.995), $decimals),
                        'stop_price' => round(min($low48, $price * 0.982), $decimals),
                        'target_price' => round($price * 1.08, $decimals) . ' (+8%) или закрытие дня < SMA50 (' . round($dSma50, $decimals) . ')',
                        'cancel_rule' => 'Ждать касания лимита до 24 часов при сохранении 1h SMA24 > SMA72',
                    ];
                }
            } else {
                $regime = 'BULL_WAIT_PULLBACK';
                $regimeTitle = 'БЫЧИЙ РЕЖИМ 1D — Ожидание выравнивания 4h SMA18 и 1h SMA24 > SMA72';
            }
        } elseif ($twoBear && $slope5d < 0 && $dayDist50 < 0) {
            if ($oversoldGuard) {
                $regime = 'OVERSOLD_COOLDOWN';
                $regimeTitle = 'ЗАЩИТА ОТ ПЕРЕПРОДАННОСТИ (RSI < 30 — шорт дна заблокирован)';
            } else {
                $regime = 'BEAR_WAVE_ACTIVE';
                $regimeTitle = 'МЕДВЕЖИЙ РЕЖИМ ПОДТВЕРЖДЁН (1d Close < падающей SMA50, RSI >= 30)';
                $recommended = [
                    'variant' => 'short_lim_2pct_day50 / short_lim_high_h4sma18 (Рукав 3: Медвежий отскок)',
                    'side' => 'SHORT (или 100% КЭШ USDT на споте)',
                    'limit_price' => round(max($barHigh, $price * 1.01), $decimals),
                    'stop_price' => round(max($high24, $price * 1.03), $decimals),
                    'target_price' => 'Выход по закрытию 4h > 4h SMA18 (' . round($h4Sma18, $decimals) . ') или 1d > SMA50',
                    'cancel_rule' => 'Отменить лимит через 24 часа, если отскок не достал уровень',
                ];
            }
        }

        return [
            'ok' => true,
            'symbol' => $symbol,
            'timestamp_utc' => gmdate('Y-m-d H:i:s') . ' UTC',
            'price' => $price,
            'decimals' => $decimals,
            'regime' => $regime,
            'regime_title' => $regimeTitle,
            'indicators' => [
                'h1_rsi14' => round($hRsi14, 1),
                'h1_rsi7' => round($hRsi7, 1),
                'h1_sma24' => round($hSma24, $decimals),
                'h1_sma72' => round($hSma72, $decimals),
                'h4_rsi14' => round($h4Rsi14, 1),
                'h4_sma18' => round($h4Sma18, $decimals),
                'd1_rsi14' => round($dRsi14, 1),
                'd1_sma20' => round($dSma20, $decimals),
                'd1_sma50' => round($dSma50, $decimals),
                'd1_slope5d_pct' => round($slope5d * 100, 2),
                'd1_dist50_pct' => round($dayDist50 * 100, 2),
                'd1_dist20_pct' => round($dayDist20 * 100, 2),
                'two_bull' => $twoBull,
                'two_bear' => $twoBear,
                'overbought_guard' => $overboughtGuard,
                'oversold_guard' => $oversoldGuard,
            ],
            'recommended' => $recommended,
        ];
    }

    private function fetchKlines(string $symbol, string $interval, int $limit): array
    {
        $response = Http::timeout(8)->get('https://api.bybit.com/v5/market/kline', [
            'category' => 'spot',
            'symbol' => $symbol,
            'interval' => $interval,
            'limit' => $limit,
        ]);

        if (!$response->successful()) {
            throw new \RuntimeException('HTTP ' . $response->status());
        }

        $data = $response->json();
        $list = $data['result']['list'] ?? [];
        $rows = array_reverse($list);
        if (count($rows) > 1) {
            array_pop($rows);
        }

        $out = [];
        foreach ($rows as $r) {
            $out[] = [
                'ts' => (int) $r[0],
                'open' => (float) $r[1],
                'high' => (float) $r[2],
                'low' => (float) $r[3],
                'close' => (float) $r[4],
                'volume' => (float) ($r[5] ?? 0.0),
            ];
        }
        return $out;
    }

    private function sma(array $values, int $length): float
    {
        $n = count($values);
        if ($n < $length || $length <= 0) {
            return (float) end($values);
        }
        $slice = array_slice($values, -$length);
        return array_sum($slice) / $length;
    }

    private function rsi(array $closes, int $length = 14): float
    {
        $n = count($closes);
        if ($n <= $length) {
            return 50.0;
        }
        $gains = [];
        $losses = [];
        for ($i = 1; $i < $n; $i++) {
            $d = $closes[$i] - $closes[$i - 1];
            $gains[] = $d > 0 ? $d : 0.0;
            $losses[] = $d < 0 ? -$d : 0.0;
        }
        $avgG = array_sum(array_slice($gains, 0, $length)) / $length;
        $avgL = array_sum(array_slice($losses, 0, $length)) / $length;
        for ($i = $length; $i < count($gains); $i++) {
            $avgG = ($avgG * ($length - 1) + $gains[$i]) / $length;
            $avgL = ($avgL * ($length - 1) + $losses[$i]) / $length;
        }
        if ($avgL == 0.0) {
            return 100.0;
        }
        return 100.0 - (100.0 / (1.0 + $avgG / $avgL));
    }
}
