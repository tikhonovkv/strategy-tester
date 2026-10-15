<?php

namespace App\Services;

use Illuminate\Support\Facades\Http;

class TelegramBotService
{
    private function readEnv(string $key, string $default = ''): string
    {
        $val = getenv($key);
        if ($val !== false && trim((string) $val) !== '') {
            return trim((string) $val);
        }
        if (!empty($_SERVER[$key]) && trim((string) $_SERVER[$key]) !== '') {
            return trim((string) $_SERVER[$key]);
        }
        if (!empty($_ENV[$key]) && trim((string) $_ENV[$key]) !== '') {
            return trim((string) $_ENV[$key]);
        }
        $laravelVal = env($key, $default);
        return trim((string) $laravelVal);
    }

    public function getConfig(): array
    {
        $token = $this->readEnv('TELEGRAM_BOT_TOKEN', '');
        $chatId = $this->readEnv('TELEGRAM_CHAT_ID', '');
        $baseUrl = rtrim($this->readEnv('TELEGRAM_BASE_URL', 'https://api.telegram.org'), '/');
        if ($baseUrl === '') {
            $baseUrl = 'https://api.telegram.org';
        }

        $notifyRaw = strtolower($this->readEnv('TELEGRAM_NOTIFY_EVERY_HOUR', 'true'));
        $notifyEveryHour = !in_array($notifyRaw, ['0', 'false', 'no', 'off'], true);

        $symbolsRaw = $this->readEnv(
            'SCAN_SYMBOLS',
            'BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT,BNBUSDT,DOGEUSDT,ADAUSDT,AVAXUSDT,LINKUSDT,SUIUSDT'
        );
        $symbols = array_values(array_filter(array_map('trim', explode(',', strtoupper($symbolsRaw)))));

        return [
            'configured' => ($token !== '' && $chatId !== ''),
            'token' => $token,
            'token_masked' => $token !== '' ? (substr($token, 0, 4) . '...' . substr($token, -4)) : 'не задан (TELEGRAM_BOT_TOKEN)',
            'chat_id' => $chatId,
            'base_url' => $baseUrl,
            'notify_every_hour' => $notifyEveryHour,
            'symbols' => $symbols,
        ];
    }

    public function sendHtml(string $htmlText): array
    {
        $cfg = $this->getConfig();
        if (!$cfg['configured']) {
            return [
                'ok' => false,
                'error' => 'Не заданы переменные окружения TELEGRAM_BOT_TOKEN или TELEGRAM_CHAT_ID.',
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
}
