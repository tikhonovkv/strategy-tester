<?php

namespace App\Console\Commands;

use App\Services\AutonomousHourlyService;
use Illuminate\Console\Command;

class ScannerDaemonCommand extends Command
{
    protected $signature = 'scanner:daemon';
    protected $description = 'Фоновый автономный демон, запускающий почасовой обход всех монет и отправку в Telegram';

    public function handle(AutonomousHourlyService $service): int
    {
        $lockFile = storage_path('app/scanner_daemon.lock');
        $fp = @fopen($lockFile, 'c+');
        if (!$fp || !flock($fp, LOCK_EX | LOCK_NB)) {
            return self::SUCCESS;
        }

        while (true) {
            try {
                $state = $service->getState();
                $state['daemon_heartbeat_utc'] = gmdate('Y-m-d H:i:s') . ' UTC';
                $service->saveState($state);

                $minute = (int) gmdate('i');
                // Run at :01 of every hour, or immediately if the current hour hasn't been scanned yet
                if ($minute >= 1) {
                    $service->runIfNewHour();
                }
            } catch (\Throwable $e) {
                // Keep daemon alive across transient network glitches
            }

            sleep(30);
        }
    }
}
