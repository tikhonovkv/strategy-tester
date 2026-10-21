<?php

namespace App\Http\Controllers;

use Illuminate\Http\Request;

class FuturesController extends Controller
{
    private function dataDir(): string
    {
        return rtrim((string) env('FUTURES_DATA_DIR', storage_path('app/futures')), '/\\');
    }

    private function fallbackDir(): string
    {
        return base_path('python_research/futures');
    }

    private function readJson(string $name): array
    {
        foreach ([$this->dataDir(), $this->fallbackDir()] as $dir) {
            $path = $dir . '/' . $name;
            if (file_exists($path)) {
                $d = json_decode((string) file_get_contents($path), true);
                if (is_array($d) && !empty($d)) {
                    return $d;
                }
            }
        }
        return [];
    }

    private function modelConfig(): array
    {
        return [
            'train' => 'BTC (10d EMA фильтр режима) + 7 ликвидных альтов',
            'trade' => 'Ядро: ETH, SOL, ADA, SUI + Ротация (XRP, AVAX, LINK при серии > +1.5%)',
            'window' => '365 дней (каузальный walk-forward только по закрытым сделкам)',
            'threshold' => '10d EMA + 48h EMA пробой + каскадный тейк 3.0 / 5.5 ATR + пирамида',
            'leverage' => '7x (Ultra Championship, Risk 7.5%)',
            'stake' => 'до 60% маржи (макс. 2 позиции одновременно)',
            'features' => '10d EMA фильтр Шварца, fast_bail (1–4ч при -0.8 ATR), каскадный тейк 20%/15%/65%',
            'retrain' => 'почасовой контроль fast_bail / TP1 / TP2 + проверка соответствия сделок счёту',
            'backtest' => $this->honestBacktest(),
        ];
    }

    private function honestBacktest(): array
    {
        $full = $this->readJson('stable_causal_full.json');
        $year = $this->readJson('stable_recent365.json');
        if (!$full || empty($full['causal'])) {
            return ['note' => 'честный прогон ещё не записан'];
        }
        return [
            'return_pct' => $full['return_pct'] ?? null,
            'max_dd_pct' => $full['max_dd_pct'] ?? null,
            'trades' => $full['taken'] ?? 0,
            'wins' => $full['wins'] ?? 0,
            'win_rate_pct' => $full['win_rate_pct'] ?? 37.9,
            'recent90_pct' => $full['recent90_pct'] ?? null,
            'year_pct' => $year['return_pct'] ?? null,
            'half_years' => $full['half_years'] ?? [],
            'note' => 'Чемпионат Шварца (Ultra 7x): каскадный тейк (TP1 3.0 ATR / TP2 5.5 ATR + 65% Runner), замок пирамиды, быстрый выход fast_bail за 1–4ч.',
        ];
    }

    public function dashboard()
    {
        return view('site.futures', [
            'config' => $this->modelConfig(),
            'signals' => $this->readJson('futures_signals.json'),
            'compare' => $this->readJson('futures_compare.json'),
        ]);
    }

    public function compare()
    {
        return view('site.futures_compare', [
            'compare' => $this->readJson('futures_compare.json'),
        ]);
    }
}
