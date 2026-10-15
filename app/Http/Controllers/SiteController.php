<?php

namespace App\Http\Controllers;

use App\Services\MtfStrategyScanner;
use Illuminate\Http\Request;

class SiteController extends Controller
{
    private function getBacktestData(): array
    {
        $path = storage_path('app/strategy_data.json');
        if (file_exists($path)) {
            $decoded = json_decode(file_get_contents($path), true);
            if (is_array($decoded)) {
                return $decoded;
            }
        }
        return [];
    }

    public function index()
    {
        $data = $this->getBacktestData();
        return view('site.index', [
            'data' => $data,
            'variants' => MtfStrategyScanner::VARIANTS,
        ]);
    }

    public function variants()
    {
        $data = $this->getBacktestData();
        return view('site.variants', [
            'data' => $data,
            'variants' => MtfStrategyScanner::VARIANTS,
        ]);
    }

    public function scanner(Request $request, MtfStrategyScanner $scanner)
    {
        $symbol = strtoupper($request->query('symbol', 'BTCUSDT'));
        $scan = $scanner->scanBybitSpot($symbol);
        return view('site.scanner', [
            'symbol' => $symbol,
            'scan' => $scan,
            'variants' => MtfStrategyScanner::VARIANTS,
        ]);
    }

    public function about()
    {
        return view('site.about');
    }

    public function apiScan(MtfStrategyScanner $scanner, string $symbol = 'BTCUSDT')
    {
        return response()->json($scanner->scanBybitSpot($symbol));
    }
}
