<?php

namespace App\Services;

use Illuminate\Support\Facades\Http;

class TelegramBotService
{
    public function getConfig(): array
    {
        $settings = $this->loadSettingsFile();

        $token = trim((string) ($settings['telegram_bot_token'] ?? env('TELEGRAM_BOT_TOKEN', '')));
        $chatId = trim((string) ($settings['telegram_chat_id'] ?? env('TELEGRAM_CHAT_ID', '')));
        $baseUrl = rtrim(trim((string) ($settings['telegram_base_url'] ?? env('TELEGRAM_BASE_URL', 'https://api.telegram.org'))), '/');
        if ($baseUrl === '') {
            $baseUrl = 'https://api.telegram.org';
        }
        $notifyEveryHour = (bool) ($settings['notify_every_hour'] ?? env('TELEGRAM_NOTIFY_EVERY_HOUR', true));

        $symbolsRaw = (string) ($settings['symbols'] ?? env('SCAN_SYMBOLS', 'BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT,BNBUSDT,DOGEUSDT,ADAUSDT,AVAXUSDT,LINKUSDT,SUIUSDT'));
        $symbols = array_values(array_filter(array_map('trim', explode(',', strtoupper($symbolsRaw)))));

        return [
            'configured' => ($token !== '' && $chatId !== ''),
            'token' => $token,
            'token_masked' => $token !== '' ? (substr($token, 0, 4) . '...' . substr($token, -4)) : 'не задан',
            'chat_id' => $chatId,
            'base_url' => $baseUrl,
            'notify_every_hour' => $notifyEveryHour,
            'symbols' => $symbols,
        ];
    }

    public function saveSettings(array $input): void
    {
        $current = $this->loadSettingsFile();

        if (!empty($input['telegram_bot_token']) && !str_contains($input['telegram_bot_token'], '...')) {
            $current['telegram_bot_token'] = trim((string) $input['telegram_bot_token']);
        }
        if (isset($input['telegram_chat_id']) && trim((string) $input['telegram_chat_id']) !== '') {
            $current['telegram_chat_id'] = trim((string) $input['telegram_chat_id']);
        }
        if (isset($input['telegram_base_url']) && trim((string) $input['telegram_base_url']) !== '') {
            $current['telegram_base_url'] = rtrim(trim((string) $input['telegram_base_url']), '/');
        }
        if (isset($input['symbols']) && trim((string) $input['symbols']) !== '') {
            $current['symbols'] = strtoupper(trim((string) $input['symbols']));
        }
        $current['notify_every_hour'] = !empty($input['notify_every_hour']);

        $path = storage_path('app/autonomous_settings.json');
        file_put_contents($path, json_encode($current, JSON_PRETTY_PRINT | JSON_UNESCAPED_UNICODE));
    }

    public function sendHtml(string $htmlText): array
    {
        $cfg = $this->getConfig();
        if (!$cfg['configured']) {
            return [
                'ok' => false,
                'error' => 'Не заданы TELEGRAM_BOT_TOKEN или TELEGRAM_CHAT_ID.',
            ];
        }

        $url = $cfg['base_url'] . '/bot' . $cfg['token'] . '/sendMessage';
        $chunk = mb_strlen($htmlText) <= 3900 ? $htmlText : (mb_substr($htmlText, 0, 3900) . "\n…");

        try {
            $resp = Http::timeout(20)->post($url, [
                'chat_id' => $cfg['chat_id'],
                'text' => $chunk,
                'parse_mode' => 'HTML',
                'disable_web_page_preview' => true,
            ]);

            if ($resp->successful() && ($resp->json('ok') === true)) {
                return ['ok' => true];
            }

            return [
                'ok' => false,
                'error' => 'Telegram HTTP ' . $resp->status() . ': ' . mb_substr($resp->body(), 0, 300),
            ];
        } catch (\Throwable $e) {
            return [
                'ok' => false,
                'error' => 'Ошибка сети Telegram: ' . $e->getMessage(),
            ];
        }
    }

    private function loadSettingsFile(): array
    {
        $path = storage_path('app/autonomous_settings.json');
        if (file_exists($path)) {
            $decoded = json_decode((string) file_get_contents($path), true);
            if (is_array($decoded)) {
                return $decoded;
            }
        }
        return [];
    }
}
