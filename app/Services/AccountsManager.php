<?php

namespace App\Services;

class AccountsManager
{
    public const CONFIG_NAME = 'accounts_config.json';
    public const BACKUP_NAME = 'accounts_config.bak.json';
    public const DEFAULT_MASTER_PIN = '15405021';
    private const ENC_PREFIX = 'enc1:';

    private static function cipherSecret(): string
    {
        $cfg = TimewebState::settings();
        return (string) (
            env('ACCOUNTS_CIPHER_KEY')
            ?: ($cfg['secret'] ?? '')
            ?: env('S3_SECRET_KEY')
            ?: env('AWS_SECRET_ACCESS_KEY')
            ?: 'bybit-default-cipher-key-540502'
        );
    }

    private static function keystream(string $masterBytes, string $nonce, int $length): string
    {
        $out = '';
        $counter = 0;
        while (strlen($out) < $length) {
            $out .= hash_hmac('sha256', $nonce . pack('N', $counter), $masterBytes, true);
            $counter++;
        }
        return substr($out, 0, $length);
    }

    public static function encryptSecret(string $plain): string
    {
        if ($plain === '' || str_starts_with($plain, self::ENC_PREFIX)) {
            return $plain;
        }
        $master = hash('sha256', 'accounts-cipher:' . self::cipherSecret(), true);
        $nonce = random_bytes(12);
        $stream = self::keystream($master, $nonce, strlen($plain));
        $cipher = $plain ^ $stream;
        $mac = substr(hash_hmac('sha256', $nonce . $cipher, $master, true), 0, 16);
        $b64 = rtrim(strtr(base64_encode($nonce . $mac . $cipher), '+/', '-_'), '=');
        return self::ENC_PREFIX . $b64;
    }

    public static function decryptSecret(string $value): string
    {
        if ($value === '' || !str_starts_with($value, self::ENC_PREFIX)) {
            return $value;
        }
        $b64 = substr($value, strlen(self::ENC_PREFIX));
        $pad = strlen($b64) % 4;
        if ($pad > 0) {
            $b64 .= str_repeat('=', 4 - $pad);
        }
        $packed = base64_decode(strtr($b64, '-_', '+/'), true);
        if (!is_string($packed) || strlen($packed) < 28) {
            return '';
        }
        $nonce = substr($packed, 0, 12);
        $mac = substr($packed, 12, 16);
        $cipher = substr($packed, 28);
        $master = hash('sha256', 'accounts-cipher:' . self::cipherSecret(), true);
        $expected = substr(hash_hmac('sha256', $nonce . $cipher, $master, true), 0, 16);
        if (!hash_equals($mac, $expected)) {
            return '';
        }
        $stream = self::keystream($master, $nonce, strlen($cipher));
        return $cipher ^ $stream;
    }

    public static function maskKey(string $key): string
    {
        $plain = self::decryptSecret($key);
        if ($plain === '') {
            return 'не задан';
        }
        if (strlen($plain) <= 6) {
            return '****';
        }
        return substr($plain, 0, 3) . '••••' . substr($plain, -4);
    }

    private static function localPaths(): array
    {
        $dataDir = rtrim((string) env('FUTURES_DATA_DIR', storage_path('app/futures')), '/\\');
        $fallbackDir = base_path('python_research/futures');
        return array_unique([$fallbackDir . '/' . self::CONFIG_NAME, $dataDir . '/' . self::CONFIG_NAME]);
    }

    public static function defaultConfig(): array
    {
        return [
            'version' => 1,
            'master_pin' => self::DEFAULT_MASTER_PIN,
            'updated_at' => gmdate('Y-m-d H:i:s \U\T\C'),
            'accounts' => [
                [
                    'id' => 'main',
                    'name' => 'Основной Bybit (7x)',
                    'enabled' => true,
                    'pause_new_trades' => false,
                    'pin' => self::DEFAULT_MASTER_PIN,
                    'exchange' => 'bybit',
                    'strategy' => 'schwartz_7x_sleeve_3x',
                    'leverage' => 7.0,
                    'sleeve_leverage' => 3.0,
                    'risk_pct' => 0.075,
                    'max_margin_frac' => 0.60,
                    'max_conc' => 2,
                    'enable_sleeve' => true,
                    'api_key' => '',
                    'api_secret' => '',
                ],
            ],
        ];
    }

    private static function normalizeAccount(array $raw, bool $isFirst = false): array
    {
        $id = strtolower(trim((string) ($raw['id'] ?? ($isFirst ? 'main' : 'bot2'))));
        $id = preg_replace('/[^a-z0-9_-]/', '', $id) ?: 'main';
        $strategy = (string) ($raw['strategy'] ?? 'schwartz_7x_sleeve_3x');
        $enableSleeve = isset($raw['enable_sleeve']) ? (bool) $raw['enable_sleeve'] : ($strategy !== 'schwartz_only');
        $apiKey = self::decryptSecret((string) ($raw['api_key'] ?? ''));
        $apiSecret = self::decryptSecret((string) ($raw['api_secret'] ?? ''));

        return [
            'id' => $id,
            'name' => trim((string) ($raw['name'] ?? ('Бот ' . $id))),
            'enabled' => (bool) ($raw['enabled'] ?? true),
            'pause_new_trades' => (bool) ($raw['pause_new_trades'] ?? false),
            'pin' => trim((string) ($raw['pin'] ?? self::DEFAULT_MASTER_PIN)),
            'exchange' => strtolower(trim((string) ($raw['exchange'] ?? 'bybit'))),
            'strategy' => $strategy,
            'leverage' => max(1.0, min(50.0, (float) ($raw['leverage'] ?? 7.0))),
            'sleeve_leverage' => max(1.0, min(20.0, (float) ($raw['sleeve_leverage'] ?? 3.0))),
            'risk_pct' => max(0.005, min(0.50, (float) ($raw['risk_pct'] ?? 0.075))),
            'max_margin_frac' => max(0.10, min(0.95, (float) ($raw['max_margin_frac'] ?? 0.60))),
            'max_conc' => max(1, min(10, (int) ($raw['max_conc'] ?? 2))),
            'enable_sleeve' => $enableSleeve,
            'api_key' => $apiKey,
            'api_secret' => $apiSecret,
            'has_keys' => ($apiKey !== '' && $apiSecret !== '') || $id === 'main',
            'masked_key' => ($apiKey !== '') ? self::maskKey($apiKey) : ($id === 'main' ? 'из окружения / S3' : 'не задан'),
        ];
    }

    public static function loadConfig(): array
    {
        $data = TimewebState::readJson(self::CONFIG_NAME);
        if (empty($data['accounts'])) {
            foreach (self::localPaths() as $path) {
                if (is_file($path)) {
                    $cand = json_decode((string) file_get_contents($path), true);
                    if (is_array($cand) && !empty($cand['accounts'])) {
                        $data = $cand;
                        break;
                    }
                }
            }
        }
        if (empty($data['accounts'])) {
            $data = self::defaultConfig();
        }

        $accounts = [];
        $seen = [];
        foreach (array_values($data['accounts'] ?? []) as $idx => $raw) {
            if (!is_array($raw)) {
                continue;
            }
            $norm = self::normalizeAccount($raw, $idx === 0);
            if (isset($seen[$norm['id']])) {
                continue;
            }
            $seen[$norm['id']] = true;
            $accounts[] = $norm;
        }
        if (!$accounts) {
            $accounts[] = self::normalizeAccount([], true);
        }

        return [
            'version' => (int) ($data['version'] ?? 1),
            'master_pin' => trim((string) ($data['master_pin'] ?? self::DEFAULT_MASTER_PIN)),
            'updated_at' => (string) ($data['updated_at'] ?? ''),
            'accounts' => $accounts,
        ];
    }

    public static function saveConfig(array $cfg): bool
    {
        $stored = [];
        foreach (array_values($cfg['accounts'] ?? []) as $idx => $acc) {
            $norm = self::normalizeAccount($acc, $idx === 0);
            $stored[] = [
                'id' => $norm['id'],
                'name' => $norm['name'],
                'enabled' => $norm['enabled'],
                'pause_new_trades' => $norm['pause_new_trades'],
                'pin' => $norm['pin'],
                'exchange' => $norm['exchange'],
                'strategy' => $norm['strategy'],
                'leverage' => $norm['leverage'],
                'sleeve_leverage' => $norm['sleeve_leverage'],
                'risk_pct' => $norm['risk_pct'],
                'max_margin_frac' => $norm['max_margin_frac'],
                'max_conc' => $norm['max_conc'],
                'enable_sleeve' => $norm['enable_sleeve'],
                'api_key' => $norm['api_key'] !== '' ? self::encryptSecret($norm['api_key']) : '',
                'api_secret' => $norm['api_secret'] !== '' ? self::encryptSecret($norm['api_secret']) : '',
            ];
        }
        $payload = [
            'version' => (int) ($cfg['version'] ?? 1),
            'master_pin' => trim((string) ($cfg['master_pin'] ?? self::DEFAULT_MASTER_PIN)),
            'updated_at' => gmdate('Y-m-d H:i:s \U\T\C'),
            'accounts' => $stored,
        ];

        foreach (self::localPaths() as $path) {
            $dir = dirname($path);
            if (is_dir($dir) && is_writable($dir)) {
                @file_put_contents($path, json_encode($payload, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_PRETTY_PRINT));
            }
        }
        $prev = TimewebState::readJson(self::CONFIG_NAME);
        if (!empty($prev['accounts'])) {
            TimewebState::writeJson(self::BACKUP_NAME, $prev);
        }
        return TimewebState::writeJson(self::CONFIG_NAME, $payload);
    }

    public static function verifyPin(string $pin): ?array
    {
        $pin = trim($pin);
        if ($pin === '') {
            return null;
        }
        $cfg = self::loadConfig();
        $masterPin = $cfg['master_pin'] ?: self::DEFAULT_MASTER_PIN;
        if (hash_equals($masterPin, $pin) || hash_equals(self::DEFAULT_MASTER_PIN, $pin)) {
            return [
                'is_master' => true,
                'account_id' => $cfg['accounts'][0]['id'] ?? 'main',
            ];
        }
        foreach ($cfg['accounts'] as $acc) {
            if (!empty($acc['pin']) && hash_equals((string) $acc['pin'], $pin)) {
                return [
                    'is_master' => false,
                    'account_id' => $acc['id'],
                ];
            }
        }
        return null;
    }

    public static function upsertAccount(array $input): array
    {
        $cfg = self::loadConfig();
        $id = strtolower(trim((string) ($input['id'] ?? '')));
        $id = preg_replace('/[^a-z0-9_-]/', '', $id);
        if ($id === '') {
            $id = 'bot' . (count($cfg['accounts']) + 1);
        }

        $existing = null;
        $existingIdx = null;
        foreach ($cfg['accounts'] as $idx => $acc) {
            if ($acc['id'] === $id) {
                $existing = $acc;
                $existingIdx = $idx;
                break;
            }
        }

        $strategy = (string) ($input['strategy'] ?? ($existing['strategy'] ?? 'schwartz_7x_sleeve_3x'));
        $enableSleeve = ($strategy !== 'schwartz_only');
        $newKey = trim((string) ($input['api_key'] ?? ''));
        $newSecret = trim((string) ($input['api_secret'] ?? ''));
        $riskInput = (float) ($input['risk_pct'] ?? (($existing['risk_pct'] ?? 0.075) * 100));
        if ($riskInput > 0.5) {
            $riskInput = $riskInput / 100.0;
        }
        $marginInput = (float) ($input['max_margin_frac'] ?? (($existing['max_margin_frac'] ?? 0.60) * 100));
        if ($marginInput > 1.0) {
            $marginInput = $marginInput / 100.0;
        }

        $updated = [
            'id' => $id,
            'name' => trim((string) ($input['name'] ?? ($existing['name'] ?? ('Бот ' . $id)))),
            'enabled' => isset($input['enabled']) ? (bool) $input['enabled'] : false,
            'pause_new_trades' => isset($input['pause_new_trades']) ? (bool) $input['pause_new_trades'] : false,
            'pin' => trim((string) ($input['pin'] ?? ($existing['pin'] ?? self::DEFAULT_MASTER_PIN))),
            'exchange' => strtolower(trim((string) ($input['exchange'] ?? ($existing['exchange'] ?? 'bybit')))),
            'strategy' => $strategy,
            'leverage' => max(1.0, min(50.0, (float) ($input['leverage'] ?? ($existing['leverage'] ?? 7.0)))),
            'sleeve_leverage' => max(1.0, min(20.0, (float) ($input['sleeve_leverage'] ?? ($existing['sleeve_leverage'] ?? 3.0)))),
            'risk_pct' => round(max(0.005, min(0.50, $riskInput)), 4),
            'max_margin_frac' => round(max(0.10, min(0.95, $marginInput)), 4),
            'max_conc' => max(1, min(10, (int) ($input['max_conc'] ?? ($existing['max_conc'] ?? 2)))),
            'enable_sleeve' => $enableSleeve,
            'api_key' => $newKey !== '' ? $newKey : ($existing['api_key'] ?? ''),
            'api_secret' => $newSecret !== '' ? $newSecret : ($existing['api_secret'] ?? ''),
        ];

        if ($existingIdx !== null) {
            $cfg['accounts'][$existingIdx] = $updated;
        } else {
            $cfg['accounts'][] = $updated;
        }
        $ok = self::saveConfig($cfg);
        return ['ok' => $ok, 'id' => $id];
    }

    public static function accountAction(string $id, string $action): bool
    {
        $cfg = self::loadConfig();
        $id = strtolower(trim($id));
        if ($action === 'delete') {
            if ($id === 'main' || count($cfg['accounts']) <= 1) {
                return false;
            }
            $cfg['accounts'] = array_values(array_filter(
                $cfg['accounts'],
                fn ($a) => ($a['id'] ?? '') !== $id
            ));
            return self::saveConfig($cfg);
        }
        foreach ($cfg['accounts'] as &$acc) {
            if ($acc['id'] === $id) {
                if ($action === 'toggle') {
                    $acc['enabled'] = empty($acc['enabled']);
                } elseif ($action === 'enable') {
                    $acc['enabled'] = true;
                } elseif ($action === 'disable') {
                    $acc['enabled'] = false;
                } elseif ($action === 'toggle_pause') {
                    $acc['pause_new_trades'] = empty($acc['pause_new_trades']);
                } elseif ($action === 'pause') {
                    $acc['pause_new_trades'] = true;
                } elseif ($action === 'resume') {
                    $acc['pause_new_trades'] = false;
                }
                break;
            }
        }
        unset($acc);
        return self::saveConfig($cfg);
    }
}
