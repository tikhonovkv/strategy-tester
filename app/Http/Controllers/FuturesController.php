<?php

namespace App\Http\Controllers;

use Illuminate\Http\Request;

class FuturesController extends Controller
{
    private function dataDir(): string
    {
        return rtrim((string) env('FUTURES_DATA_DIR', storage_path('app/futures')), '/\\');
    }

    private function readJson(string $name): array
    {
        $path = $this->dataDir() . '/' . $name;
        if (!file_exists($path)) {
            return [];
        }
        $d = json_decode((string) file_get_contents($path), true);
        return is_array($d) ? $d : [];
    }

    private function modelConfig(): array
    {
        return [
            'train' => 'BTC, ETH, SOL, XRP, BNB, ADA, AVAX, LINK, SUI (9 монет)',
            'trade' => 'ETH, SOL, ADA, SUI (4 монеты)',
            'window' => '180 дней',
            'threshold' => '0.6%',
            'leverage' => '3x',
            'stake' => '20% на позицию (до 4 одновременно)',
            'features' => '8 funding + z-score + delta + 6 price returns',
            'retrain' => 'ежедневно, скользящее окно 180 дней',
            'backtest' => [
                'trades' => 2104,
                'years' => '5.7',
                'lev2' => '+3852%',
                'lev3' => '+16514%',
                'lev4' => '+55844%',
            ],
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
