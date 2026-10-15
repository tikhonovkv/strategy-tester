<?php

namespace App\Services;

class AutonomousHourlyService
{
    private MtfStrategyScanner $scanner;
    private TelegramBotService $telegram;

    public function __construct(MtfStrategyScanner $scanner, TelegramBotService $telegram)
    {
        $this->scanner = $scanner;
        $this->telegram = $telegram;
    }

    public function getState(): array
    {
        $path = storage_path('app/autonomous_state.json');
        if (file_exists($path)) {
            $decoded = json_decode((string) file_get_contents($path), true);
            if (is_array($decoded)) {
                return $decoded;
            }
        }
        return [
            'last_hour_key' => null,
            'last_run_utc' => null,
            'daemon_heartbeat_utc' => null,
            'latest_results' => [],
            'active_tasks' => [],
            'history_runs' => [],
            'telegram_log' => [],
        ];
    }

    public function saveState(array $state): void
    {
        $path = storage_path('app/autonomous_state.json');
        file_put_contents($path, json_encode($state, JSON_PRETTY_PRINT | JSON_UNESCAPED_UNICODE));
    }

    public function ensureDaemonRunning(): void
    {
        $state = $this->getState();
        $hb = $state['daemon_heartbeat_utc'] ?? null;
        if ($hb && (time() - strtotime($hb)) < 120) {
            return;
        }

        if (stripos(PHP_OS_FAMILY, 'Windows') === false && function_exists('exec')) {
            $artisan = base_path('artisan');
            $cmd = 'nohup php ' . escapeshellarg($artisan) . ' scanner:daemon > /dev/null 2>&1 &';
            @exec($cmd);
        }
    }

    public function runIfNewHour(): ?array
    {
        $hourKey = gmdate('Y-m-d H:00');
        $state = $this->getState();
        if (($state['last_hour_key'] ?? null) === $hourKey) {
            return null;
        }
        return $this->runHourlyScan(false);
    }

    public function runHourlyScan(bool $forceTelegram = false): array
    {
        $cfg = $this->telegram->getConfig();
        $symbols = $cfg['symbols'];
        $hourKey = gmdate('Y-m-d H:00');
        $nowUtc = gmdate('Y-m-d H:i:s') . ' UTC';

        $state = $this->getState();
        $state['last_hour_key'] = $hourKey;
        $state['last_run_utc'] = $nowUtc;

        $results = [];
        $newTasks = [];
        $updatedTasksInfo = [];

        $activeTasks = $state['active_tasks'] ?? [];

        foreach ($symbols as $sym) {
            $scan = $this->scanner->scanBybitSpot($sym);
            $results[$sym] = $scan;

            if (!($scan['ok'] ?? false)) {
                continue;
            }

            $price = (float) $scan['price'];
            $ind = $scan['indicators'];

            // 1. Check existing open task for this symbol
            if (isset($activeTasks[$sym])) {
                $task = $activeTasks[$sym];
                $isLong = str_contains((string) $task['side'], 'LONG');
                $lim = (float) $task['limit_price'];
                $sl  = (float) $task['stop_price'];

                if (($task['status'] ?? '') === 'PENDING_LIMIT') {
                    if (($isLong && $price <= $lim) || (!$isLong && $price >= $lim)) {
                        $activeTasks[$sym]['status'] = 'ACTIVE_POSITION';
                        $activeTasks[$sym]['filled_at_utc'] = $nowUtc;
                        $updatedTasksInfo[] = "⚡️ <b>{$sym}</b>: Лимитный ордер <b>ИСПОЛНЕН</b> по <code>\${$lim}</code> (Стоп: <code>\${$sl}</code>)";
                    } elseif (($isLong && $ind['h1_sma24'] <= $ind['h1_sma72']) || (!$isLong && $ind['h1_sma24'] >= $ind['h1_sma72'])) {
                        unset($activeTasks[$sym]);
                        $updatedTasksInfo[] = "🚫 <b>{$sym}</b>: Несработавший лимит <code>\${$lim}</code> <b>СНЯТ</b> (часовой импульс SMA24/72 развернулся).";
                    }
                } elseif (($task['status'] ?? '') === 'ACTIVE_POSITION') {
                    if (($isLong && $price <= $sl) || (!$isLong && $price >= $sl)) {
                        unset($activeTasks[$sym]);
                        $updatedTasksInfo[] = "🛑 <b>{$sym}</b>: Позиция закрыта по <b>СТОП-ЛОССУ</b> (<code>\${$sl}</code>, текущая <code>\${$price}</code>).";
                    } elseif (($isLong && $price < (float) $ind['d1_sma50']) || (!$isLong && $price > (float) $ind['d1_sma50'])) {
                        unset($activeTasks[$sym]);
                        $updatedTasksInfo[] = "✅ <b>{$sym}</b>: Позиция закрыта по пересечению <b>1d SMA50</b> (цена <code>\${$price}</code>).";
                    }
                }
            }

            // 2. If no active task on this symbol and a new signal fired, create task
            if (!isset($activeTasks[$sym]) && !empty($scan['recommended'])) {
                $rec = $scan['recommended'];
                $task = [
                    'symbol' => $sym,
                    'created_at_utc' => $nowUtc,
                    'hour_key' => $hourKey,
                    'status' => 'PENDING_LIMIT',
                    'variant' => $rec['variant'],
                    'side' => $rec['side'],
                    'signal_close' => $price,
                    'limit_price' => $rec['limit_price'],
                    'stop_price' => $rec['stop_price'],
                    'target_price' => $rec['target_price'],
                    'cancel_rule' => $rec['cancel_rule'],
                ];
                $activeTasks[$sym] = $task;
                $newTasks[] = $task;
            }
        }

        $state['latest_results'] = $results;
        $state['active_tasks'] = $activeTasks;

        $runSummary = [
            'hour_key' => $hourKey,
            'timestamp_utc' => $nowUtc,
            'symbols_scanned' => count($results),
            'new_signals_count' => count($newTasks),
            'active_tasks_count' => count($activeTasks),
        ];

        $history = $state['history_runs'] ?? [];
        array_unshift($history, $runSummary);
        $state['history_runs'] = array_slice($history, 0, 48);

        // 3. Send Telegram notification if there are new tasks, status updates, forced test, or hourly report is enabled
        $shouldSend = $forceTelegram || !empty($newTasks) || !empty($updatedTasksInfo) || $cfg['notify_every_hour'];
        $tgResult = ['ok' => false, 'skipped' => true];

        if ($shouldSend && $cfg['configured']) {
            $msg = $this->formatTelegramMessage($hourKey, $results, $newTasks, $updatedTasksInfo, $activeTasks);
            $tgResult = $this->telegram->sendHtml($msg);

            $tgLog = $state['telegram_log'] ?? [];
            array_unshift($tgLog, [
                'timestamp_utc' => $nowUtc,
                'ok' => $tgResult['ok'] ?? false,
                'error' => $tgResult['error'] ?? null,
                'new_tasks' => count($newTasks),
            ]);
            $state['telegram_log'] = array_slice($tgLog, 0, 30);
        }

        $this->saveState($state);

        return [
            'ok' => true,
            'hour_key' => $hourKey,
            'timestamp_utc' => $nowUtc,
            'symbols_scanned' => count($results),
            'new_tasks' => $newTasks,
            'active_tasks' => $activeTasks,
            'telegram' => $tgResult,
        ];
    }

    private function formatTelegramMessage(
        string $hourKey,
        array $results,
        array $newTasks,
        array $updatedTasksInfo,
        array $activeTasks
    ): string {
        $lines = [];
        $lines[] = "📊 <b>Strategy Tester MTF — Почасовой скан ({$hourKey} UTC)</b>";
        $lines[] = "";

        if (!empty($newTasks)) {
            $lines[] = "🎯 <b>НОВЫЕ ЗАДАЧИ НА ОТКРЫТИЕ СДЕЛКИ (" . count($newTasks) . "):</b>";
            foreach ($newTasks as $t) {
                $icon = str_contains((string) $t['side'], 'LONG') ? '🟢' : '🔴';
                $lines[] = "{$icon} <b>{$t['symbol']}</b> — <b>{$t['side']}</b>";
                $lines[] = "  • Вариант: <code>{$t['variant']}</code>";
                $lines[] = "  • Вход (Limit): <code>\${$t['limit_price']}</code> (текущая: \${$t['signal_close']})";
                $lines[] = "  • Стоп-лосс (SL): <code>\${$t['stop_price']}</code>";
                $lines[] = "  • Выход / Тейк: {$t['target_price']}";
                $lines[] = "  • Отмена: {$t['cancel_rule']}";
                $lines[] = "";
            }
        } else {
            $lines[] = "⏳ <b>Новых точек входа на этом часе нет.</b>";
            $lines[] = "";
        }

        if (!empty($updatedTasksInfo)) {
            $lines[] = "🔔 <b>Обновления по ордерам:</b>";
            foreach ($updatedTasksInfo as $info) {
                $lines[] = "  • " . $info;
            }
            $lines[] = "";
        }

        $lines[] = "📈 <b>Сводка по монетам (" . count($results) . "):</b>";
        foreach ($results as $sym => $r) {
            if (!($r['ok'] ?? false)) {
                $lines[] = "  • <b>{$sym}</b>: ошибка API";
                continue;
            }
            $ind = $r['indicators'];
            $reg = $r['regime'];
            $badge = match ($reg) {
                'BULL_WAVE_ACTIVE' => '🟢 BULL_ACTIVE',
                'BEAR_WAVE_ACTIVE' => '🔴 BEAR_ACTIVE',
                'OVERBOUGHT_COOLDOWN' => '⚠️ OVERBOUGHT (RSI>70)',
                'OVERSOLD_COOLDOWN' => '⚠️ OVERSOLD (RSI<30)',
                'BULL_WAIT_PULLBACK' => '🟡 BULL_WAIT_4H_1H',
                default => '⚪️ CASH',
            };
            $lines[] = sprintf(
                "  • <b>%s</b> <code>\$%s</code> | %s | RSI 1h/4h/1d: <code>%s/%s/%s</code> | SMA50: <code>%+.1f%%</code>",
                $sym,
                number_format((float) $r['price'], $r['price'] >= 100 ? 2 : 4, '.', ''),
                $badge,
                $ind['h1_rsi14'],
                $ind['h4_rsi14'],
                $ind['d1_rsi14'],
                $ind['d1_dist50_pct']
            );
        }

        if (!empty($activeTasks)) {
            $lines[] = "";
            $lines[] = "📂 <b>Активные задачи / позиции (" . count($activeTasks) . "):</b>";
            foreach ($activeTasks as $sym => $t) {
                $lines[] = "  • <b>{$sym}</b> [{$t['status']}]: {$t['side']} лимит <code>\${$t['limit_price']}</code>, стоп <code>\${$t['stop_price']}</code>";
            }
        }

        return implode("\n", $lines);
    }
}
