<?php

namespace App\Http\Controllers;

use Illuminate\Http\Request;

class FuturesController extends Controller
{
    private function readJson(string $rel): array
    {
        $path = storage_path('app/' . $rel);
        if (!file_exists($path)) {
            return [];
        }
        $decoded = json_decode((string) file_get_contents($path), true);
        return is_array($decoded) ? $decoded : [];
    }

    public function dashboard()
    {
        return view('site.futures', [
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
