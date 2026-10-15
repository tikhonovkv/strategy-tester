<?php

namespace App\Http\Controllers;

use App\Services\AutonomousHourlyService;
use App\Services\MtfStrategyScanner;
use App\Services\TelegramBotService;
use Illuminate\Http\Request;

class SiteController extends Controller
{
    private function getBacktestData(): array
    {
        $path = storage_path('app/strategy_data.json');
        if (file_exists($path)) {
            $decoded = json_decode((string) file_get_contents($path), true);
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
        $symbol = strtoupper((string) $request->query('symbol', 'BTCUSDT'));
        $scan = $scanner->scanBybitSpot($symbol);
        return view('site.scanner', [
            'symbol' => $symbol,
            'scan' => $scan,
            'variants' => MtfStrategyScanner::VARIANTS,
        ]);
    }

    public function autonomous(AutonomousHourlyService $auto, TelegramBotService $telegram)
    {
        $state = $auto->getState();
        if (empty($state['latest_results'])) {
            $auto->runHourlyScan(false);
            $state = $auto->getState();
        }
        return view('site.autonomous', [
            'state' => $state,
            'config' => $telegram->getConfig(),
        ]);
    }

    public function saveSettings(Request $request, TelegramBotService $telegram, AutonomousHourlyService $auto)
    {
        $telegram->saveSettings($request->all());

        if ($request->input('action') === 'save_and_scan') {
            $res = $auto->runHourlyScan(true);
            $tgOk = $res['telegram']['ok'] ?? false;
            $err = $res['telegram']['error'] ?? '';
            $msg = $tgOk
                ? 'Настройки сохранены, выполнен обход всех монет и отправлено сообщение в Telegram!'
                : ('Настройки сохранены, скан выполнен. Статус Telegram: ' . ($err ?: 'проверьте токен и chat_id'));
            return redirect()->route('autonomous')->with('status', $msg);
        }

        return redirect()->route('autonomous')->with('status', 'Настройки автономного бота и Telegram сохранены.');
    }

    public function runNow(AutonomousHourlyService $auto)
    {
        $res = $auto->runHourlyScan(true);
        $tgOk = $res['telegram']['ok'] ?? false;
        $err = $res['telegram']['error'] ?? '';
        $msg = sprintf(
            'Почасовой обход выполнен (%d монет, новых задач: %d). Отправка в Telegram: %s',
            $res['symbols_scanned'],
            count($res['new_tasks']),
            $tgOk ? 'УСПЕШНО' : ($err ?: 'не настроен')
        );
        return redirect()->route('autonomous')->with('status', $msg);
    }

    public function about()
    {
        return view('site.about');
    }

    public function apiScan(MtfStrategyScanner $scanner, string $symbol = 'BTCUSDT')
    {
        return response()->json($scanner->scanBybitSpot($symbol));
    }

    public function apiHourlyCron(Request $request, AutonomousHourlyService $auto)
    {
        $force = (bool) $request->query('force', false);
        if ($force) {
            return response()->json($auto->runHourlyScan(true));
        }
        $ran = $auto->runIfNewHour();
        return response()->json([
            'ok' => true,
            'triggered_new_hour' => $ran !== null,
            'hour_key' => gmdate('Y-m-d H:00'),
            'result' => $ran,
        ]);
    }
}
