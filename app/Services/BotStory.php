<?php

namespace App\Services;

class BotStory
{
    public function __construct(private string $basePath)
    {
    }

    public function build(array $sleeve, array $compare): array
    {
        $full = $sleeve['causal_full'] ?? [];
        $curve = $this->dailyEquity($full['equity_pct'] ?? []);
        if (!$curve) {
            $curve = $this->dailyEquity($this->periodEquity($compare, 'Вся история'));
            $period = $this->period($compare, 'Вся история');
            $full = [
                'return_pct' => $period['return_pct'] ?? null,
                'max_dd_pct' => $period['max_dd_pct'] ?? null,
                'final' => $period['final_eq'] ?? null,
                'schwartz_trades' => [],
                'trades' => [],
            ];
        }
        $examples = $this->examples();
        $cases = $this->cases($full, $examples['picks'] ?? []);
        $windows = [];
        foreach (['recent30' => 'Месяц', 'recent90' => 'Квартал', 'recent365' => 'Год', 'causal_full' => 'Вся история'] as $tag => $label) {
            $row = $sleeve[$tag] ?? [];
            if (!$row && $tag === 'causal_full' && isset($full['return_pct'])) {
                $row = $full;
            }
            if (!isset($row['return_pct'])) {
                continue;
            }
            $windows[] = [
                'label' => $label,
                'return_pct' => $row['return_pct'],
                'max_dd_pct' => $row['max_dd_pct'] ?? null,
                'final' => $row['final'] ?? null,
                'schwartz' => count($row['schwartz_trades'] ?? []),
                'legs' => count($row['trades'] ?? []),
            ];
        }
        return [
            'ready' => count($curve) > 1,
            'stats' => [
                'return_pct' => $full['return_pct'] ?? null,
                'max_dd_pct' => $full['max_dd_pct'] ?? null,
                'final' => $full['final'] ?? null,
                'start' => 2000,
                'schwartz' => count($full['schwartz_trades'] ?? []),
                'legs' => count($full['trades'] ?? []),
                'from' => $curve[0]['d'] ?? null,
                'to' => $curve ? $curve[count($curve) - 1]['d'] : null,
            ],
            'windows' => $windows,
            'parts' => $examples['parts'] ?? [],
            'cases' => $cases,
            'curve' => $curve,
        ];
    }

    private function examples(): array
    {
        $path = $this->basePath.'/resources/bot_examples.json';
        if (!is_file($path)) {
            return [];
        }
        $data = json_decode((string) file_get_contents($path), true);
        return is_array($data) ? $data : [];
    }

    private function period(array $compare, string $label): array
    {
        foreach ($compare['periods'] ?? [] as $row) {
            if (($row['label'] ?? '') === $label) {
                return $row;
            }
        }
        return [];
    }

    private function periodEquity(array $compare, string $label): array
    {
        $row = $this->period($compare, $label);
        return is_array($row['equity'] ?? null) ? $row['equity'] : [];
    }

    private function dailyEquity(array $points): array
    {
        $daily = [];
        foreach ($points as $pt) {
            if (!is_array($pt) || empty($pt[0])) {
                continue;
            }
            $day = substr((string) $pt[0], 0, 10);
            $pct = (float) ($pt[1] ?? 0);
            $daily[$day] = round(2000 * (1 + $pct / 100), 2);
        }
        $out = [];
        foreach ($daily as $day => $eq) {
            $out[] = ['d' => $day, 'eq' => $eq];
        }
        return $out;
    }

    private function cases(array $full, array $picks): array
    {
        $books = [
            'schwartz' => $full['schwartz_trades'] ?? [],
            'sleeve' => $full['trades'] ?? [],
        ];
        $out = [];
        foreach ($picks as $pick) {
            if (!is_array($pick)) {
                continue;
            }
            $pool = $books[$pick['book'] ?? ''] ?? [];
            $reason = (string) ($pick['reason'] ?? '');
            $pool = array_values(array_filter($pool, function ($row) use ($reason) {
                return is_array($row) && (string) ($row['reason'] ?? '') === $reason;
            }));
            if (!$pool) {
                continue;
            }
            usort($pool, function ($a, $b) use ($pick) {
                $rank = (string) ($pick['rank'] ?? 'best_pnl');
                if ($rank === 'first') {
                    return ((int) ($a['t'] ?? 0)) <=> ((int) ($b['t'] ?? 0));
                }
                $dir = $rank === 'worst_pnl' ? 1 : -1;
                return $dir * (((float) ($a['pnl_usd'] ?? 0)) <=> ((float) ($b['pnl_usd'] ?? 0)));
            });
            $out[] = $this->present($pool[0], $pick);
        }
        return $out;
    }

    private function present(array $row, array $pick): array
    {
        $t = (int) ($row['t'] ?? 0);
        $exit = (int) ($row['exit_ts'] ?? 0);
        $path = [];
        foreach (array_slice($row['path'] ?? [], 0, 48) as $px) {
            $path[] = round((float) $px, 6);
        }
        return [
            'title' => (string) ($pick['title'] ?? ''),
            'why_in' => (string) ($pick['why_in'] ?? ''),
            'why_out' => (string) ($pick['why_out'] ?? ''),
            'book' => (string) ($row['book'] ?? $pick['book'] ?? ''),
            'coin' => (string) ($row['coin'] ?? ''),
            'side' => ((int) ($row['side'] ?? 0)) === 1 ? 'лонг' : 'шорт',
            'reason' => (string) ($row['reason'] ?? ''),
            'time' => $t ? gmdate('Y-m-d H:i', (int) ($t / 1000)) : '',
            'exit_time' => $exit ? gmdate('Y-m-d H:i', (int) ($exit / 1000)) : '',
            'hold_h' => (int) ($row['hold_h'] ?? 0),
            'entry' => $row['entry_price'] ?? null,
            'exit' => $row['exit_price'] ?? null,
            'pnl_usd' => $row['pnl_usd'] ?? null,
            'pnl_pct' => $row['pnl_pct'] ?? null,
            'eq' => $row['eq'] ?? null,
            'pyr' => (bool) ($row['pyr'] ?? false),
            'path' => $path,
        ];
    }
}
