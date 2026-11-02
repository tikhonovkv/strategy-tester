<?php

namespace App\Services;

class TimewebState
{
    public static function readJson(string $name): array
    {
        $bucket = env('S3_BUCKET', env('AWS_BUCKET'));
        $access = env('S3_ACCESS_KEY', env('AWS_ACCESS_KEY_ID'));
        $secret = env('S3_SECRET_KEY', env('AWS_SECRET_ACCESS_KEY'));
        if (!$bucket || !$access || !$secret) {
            return [];
        }
        $endpoint = rtrim((string) env('S3_ENDPOINT', 'https://s3.twcstorage.ru'), '/');
        $region = (string) env('S3_REGION', 'ru-1');
        $prefix = trim((string) env('S3_PREFIX', 'bybit-futures'), '/');
        $path = '/'.$bucket.'/'.$prefix.'/'.$name;
        $host = parse_url($endpoint, PHP_URL_HOST);
        $amzDate = gmdate('Ymd\THis\Z');
        $day = gmdate('Ymd');
        $payload = hash('sha256', '');
        $canonical = implode("\n", [
            'GET',
            $path,
            '',
            'host:'.$host."\n".'x-amz-content-sha256:'.$payload."\n".'x-amz-date:'.$amzDate."\n",
            'host;x-amz-content-sha256;x-amz-date',
            $payload,
        ]);
        $scope = $day.'/'.$region.'/s3/aws4_request';
        $toSign = implode("\n", [
            'AWS4-HMAC-SHA256',
            $amzDate,
            $scope,
            hash('sha256', $canonical),
        ]);
        $key = hash_hmac('sha256', $day, 'AWS4'.$secret, true);
        $key = hash_hmac('sha256', $region, $key, true);
        $key = hash_hmac('sha256', 's3', $key, true);
        $key = hash_hmac('sha256', 'aws4_request', $key, true);
        $signature = hash_hmac('sha256', $toSign, $key);
        $auth = 'AWS4-HMAC-SHA256 Credential='.$access.'/'.$scope
            .', SignedHeaders=host;x-amz-content-sha256;x-amz-date, Signature='.$signature;
        $context = stream_context_create([
            'http' => [
                'method' => 'GET',
                'header' => "Host: {$host}\r\nx-amz-content-sha256: {$payload}\r\nx-amz-date: {$amzDate}\r\nAuthorization: {$auth}\r\n",
                'timeout' => 8,
                'ignore_errors' => true,
            ],
        ]);
        $raw = @file_get_contents($endpoint.$path, false, $context);
        if (!is_string($raw) || $raw === '') {
            return [];
        }
        $data = json_decode($raw, true);
        return is_array($data) ? $data : [];
    }
}
