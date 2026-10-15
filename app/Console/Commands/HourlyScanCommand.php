<?php

namespace App\Console\Commands;

use App\Services\AutonomousHourlyService;
use Illuminate\Console\Command;

class HourlyScanCommand extends Command
{
    protected $signature = 'scanner:hourly {--force-telegram : Принудительно отправить отчёт в Telegram}';
    protected $description = 'Почасовой обход всех монет Bybit Spot и отправка задач на открытие сделок в Telegram';

    public function handle(AutonomousHourlyService $service): int
    {
        $force = (bool) $this->option('force-telegram');
        $res = $service->runHourlyScan($force);
        $this->info(
            sprintf(
                '[%s] Scanned %d symbols, new tasks: %d, active tasks: %d, telegram ok: %s',
                $res['timestamp_utc'],
                $res['symbols_scanned'],
                count($res['new_tasks']),
                count($res['active_tasks']),
                ($res['telegram']['ok'] ?? false) ? 'YES' : 'NO'
            )
        );
        return self::SUCCESS;
    }
}
