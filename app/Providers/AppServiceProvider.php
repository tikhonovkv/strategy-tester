<?php

namespace App\Providers;

use App\Services\AutonomousHourlyService;
use Illuminate\Support\Facades\URL;
use Illuminate\Support\ServiceProvider;

class AppServiceProvider extends ServiceProvider
{
    public function register()
    {
        //
    }

    public function boot()
    {
        if ($this->app->environment('production') || isset($_SERVER['HTTP_X_FORWARDED_PROTO'])) {
            URL::forceScheme('https');
        }

        if (!$this->app->runningInConsole()) {
            register_shutdown_function(function () {
                try {
                    if (function_exists('fastcgi_finish_request')) {
                        @fastcgi_finish_request();
                    }
                    /** @var AutonomousHourlyService $auto */
                    $auto = app(AutonomousHourlyService::class);
                    $auto->ensureDaemonRunning();
                    $auto->runIfNewHour();
                } catch (\Throwable $e) {
                    // Ignore background errors
                }
            });
        }
    }
}
