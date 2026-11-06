<?php

namespace App\Services;

class TimewebState
{
    public static function settings(): array
    {
        $fileVals = self::envFile();
        $pick = function (string ...$names) use ($fileVals): string {
            foreach ($names as $name) {
                $v = env($name);
                if (is_string($v) && $v !== '') {
                    return trim($v);
                }
                $gv = getenv($name);
                if (is_string($gv) && $gv !== '') {
                    return trim($gv);
                }
                if (!empty($_ENV[$name]) && is_string($_ENV[$name])) {
                    return trim($_ENV[$name]);
                }
                if (!empty($_SERVER[$name]) && is_string($_SERVER[$name])) {
                    return trim($_SERVER[$name]);
                }
                if (!empty($fileVals[$name])) {
                    return (string) $fileVals[$name];
                }
            }
            return '';
        };

        $bucket = $pick('S3_BUCKET', 'AWS_BUCKET');
        $access = $pick('S3_ACCESS_KEY', 'AWS_ACCESS_KEY_ID');
        $secret = $pick('S3_SECRET_KEY', 'AWS_SECRET_ACCESS_KEY');
        $endpoint = rtrim($pick('S3_ENDPOINT') ?: 'https://s3.twcstorage.ru', '/');
        $region = $pick('S3_REGION') ?: 'ru-1';
        $prefix = trim($pick('S3_PREFIX') ?: 'bybit-futures', '/');

        return [
            'bucket' => $bucket,
            'access' => $access,
            'secret' => $secret,
            'endpoint' => $endpoint,
            'region' => $region,
            'prefix' => $prefix,
        ];
    }

    private static function envFile(): array
    {
        $out = [];
        // 1. Read PID 1 environment on Linux (bypasses `php artisan serve` stripping env vars when .env exists)
        if (is_readable('/proc/1/environ')) {
            $rawEnv = @file_get_contents('/proc/1/environ');
            if (is_string($rawEnv) && $rawEnv !== '') {
                foreach (explode("\0", $rawEnv) as $pair) {
                    if ($pair !== '' && str_contains($pair, '=')) {
                        [$k, $v] = explode('=', $pair, 2);
                        $v = trim($v);
                        if ($v !== '') {
                            $out[trim($k)] = $v;
                        }
                    }
                }
            }
        }
        // 2. Read shared volume S3 config written by futures-bot container
        $sharedPaths = [
            storage_path('app/futures/.s3_runtime_env.json'),
            base_path('python_research/futures/.s3_runtime_env.json'),
        ];
        foreach ($sharedPaths as $sp) {
            if (is_file($sp)) {
                $cand = json_decode((string) @file_get_contents($sp), true);
                if (is_array($cand)) {
                    foreach ($cand as $k => $v) {
                        if (is_string($v) && trim($v) !== '' && empty($out[$k])) {
                            $out[$k] = trim($v);
                        }
                    }
                }
            }
        }
        // 3. Read .env files if present
        $home = getenv('USERPROFILE') ?: getenv('HOME') ?: '';
        $paths = [
            base_path('.env'),
        ];
        if ($home) {
            $paths[] = $home . '/.cursor/bybit-top25-scan-state/bybit.env';
            $paths[] = $home . '/.cursor/wallet-bybit-arb-state/bybit.env';
        }
        foreach ($paths as $p) {
            if (!is_file($p)) {
                continue;
            }
            foreach (file($p, FILE_IGNORE_NEW_LINES | FILE_SKIP_EMPTY_LINES) ?: [] as $raw) {
                $line = trim($raw);
                if ($line === '' || str_starts_with($line, '#') || !str_contains($line, '=')) {
                    continue;
                }
                [$k, $v] = explode('=', $line, 2);
                $val = trim(trim($v), "\"'");
                if ($val !== '' && empty($out[trim($k)])) {
                    $out[trim($k)] = $val;
                }
            }
        }
        return $out;
    }

    public static function readJson(string $name): array
    {
        $cfg = self::settings();
        if ($missing = self::missingSettings($cfg)) {
            error_log('[TimewebState] readJson ' . $name . ' skipped, empty env: ' . implode(', ', $missing));
            return [];
        }
        $path = '/' . $cfg['bucket'] . '/' . $cfg['prefix'] . '/' . ltrim($name, '/');
        $host = parse_url($cfg['endpoint'], PHP_URL_HOST);
        $amzDate = gmdate('Ymd\THis\Z');
        $day = gmdate('Ymd');
        $payload = hash('sha256', '');
        $canonical = implode("\n", [
            'GET',
            $path,
            '',
            'host:' . $host . "\n" . 'x-amz-content-sha256:' . $payload . "\n" . 'x-amz-date:' . $amzDate . "\n",
            'host;x-amz-content-sha256;x-amz-date',
            $payload,
        ]);
        $scope = $day . '/' . $cfg['region'] . '/s3/aws4_request';
        $toSign = implode("\n", [
            'AWS4-HMAC-SHA256',
            $amzDate,
            $scope,
            hash('sha256', $canonical),
        ]);
        $key = hash_hmac('sha256', $day, 'AWS4' . $cfg['secret'], true);
        $key = hash_hmac('sha256', $cfg['region'], $key, true);
        $key = hash_hmac('sha256', 's3', $key, true);
        $key = hash_hmac('sha256', 'aws4_request', $key, true);
        $signature = hash_hmac('sha256', $toSign, $key);
        $auth = 'AWS4-HMAC-SHA256 Credential=' . $cfg['access'] . '/' . $scope
            . ', SignedHeaders=host;x-amz-content-sha256;x-amz-date, Signature=' . $signature;
        $context = stream_context_create([
            'http' => [
                'method' => 'GET',
                'header' => "Host: {$host}\r\nx-amz-content-sha256: {$payload}\r\nx-amz-date: {$amzDate}\r\nAuthorization: {$auth}\r\n",
                'timeout' => 8,
                'ignore_errors' => true,
            ],
            'ssl' => [
                'verify_peer' => false,
                'verify_peer_name' => false,
            ],
        ]);
        $raw = @file_get_contents($cfg['endpoint'] . $path, false, $context);
        if (!is_string($raw) || $raw === '') {
            return [];
        }
        $data = json_decode($raw, true);
        return is_array($data) ? $data : [];
    }

    /** Names of S3 settings that are empty in this process (values are never logged). */
    public static function missingSettings(array $cfg): array
    {
        $missing = [];
        foreach (['bucket' => 'S3_BUCKET', 'access' => 'S3_ACCESS_KEY', 'secret' => 'S3_SECRET_KEY'] as $key => $env) {
            if (empty($cfg[$key])) {
                $missing[] = $env;
            }
        }
        if ($missing) {
            // Names only, never values: shows which S3-like variables this process can see at all.
            $seen = array_values(array_filter(array_keys(getenv() ?: []), fn ($k) => str_starts_with((string) $k, 'S3_') || str_starts_with((string) $k, 'AWS_')));
            error_log('[TimewebState] visible S3/AWS env names: ' . ($seen ? implode(', ', $seen) : 'none'));
        }
        return $missing;
    }

    public static function writeJson(string $name, array $data): bool
    {
        $cfg = self::settings();
        if ($missing = self::missingSettings($cfg)) {
            error_log('[TimewebState] writeJson ' . $name . ' skipped, empty env: ' . implode(', ', $missing));
            return false;
        }
        $body = (string) json_encode($data, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_PRETTY_PRINT);
        $path = '/' . $cfg['bucket'] . '/' . $cfg['prefix'] . '/' . ltrim($name, '/');
        $host = parse_url($cfg['endpoint'], PHP_URL_HOST);
        $amzDate = gmdate('Ymd\THis\Z');
        $day = gmdate('Ymd');
        $payload = hash('sha256', $body);
        $contentType = 'application/json';
        $canonical = implode("\n", [
            'PUT',
            $path,
            '',
            'content-type:' . $contentType . "\n" . 'host:' . $host . "\n" . 'x-amz-content-sha256:' . $payload . "\n" . 'x-amz-date:' . $amzDate . "\n",
            'content-type;host;x-amz-content-sha256;x-amz-date',
            $payload,
        ]);
        $scope = $day . '/' . $cfg['region'] . '/s3/aws4_request';
        $toSign = implode("\n", [
            'AWS4-HMAC-SHA256',
            $amzDate,
            $scope,
            hash('sha256', $canonical),
        ]);
        $key = hash_hmac('sha256', $day, 'AWS4' . $cfg['secret'], true);
        $key = hash_hmac('sha256', $cfg['region'], $key, true);
        $key = hash_hmac('sha256', 's3', $key, true);
        $key = hash_hmac('sha256', 'aws4_request', $key, true);
        $signature = hash_hmac('sha256', $toSign, $key);
        $auth = 'AWS4-HMAC-SHA256 Credential=' . $cfg['access'] . '/' . $scope
            . ', SignedHeaders=content-type;host;x-amz-content-sha256;x-amz-date, Signature=' . $signature;
        $context = stream_context_create([
            'http' => [
                'method' => 'PUT',
                'header' => "Host: {$host}\r\nContent-Type: {$contentType}\r\nx-amz-content-sha256: {$payload}\r\nx-amz-date: {$amzDate}\r\nAuthorization: {$auth}\r\n",
                'content' => $body,
                'timeout' => 12,
                'ignore_errors' => true,
            ],
            'ssl' => [
                'verify_peer' => false,
                'verify_peer_name' => false,
            ],
        ]);
        $resp = @file_get_contents($cfg['endpoint'] . $path, false, $context);
        $status = 0;
        foreach ($http_response_header ?? [] as $h) {
            if (preg_match('#^HTTP/\S+\s+(\d{3})#', $h, $m)) {
                $status = (int) $m[1];
            }
        }
        if ($resp === false || $status < 200 || $status >= 300) {
            error_log("[TimewebState] writeJson {$name} failed: status={$status} body=" . substr((string) $resp, 0, 300));
            return false;
        }
        return true;
    }
}
