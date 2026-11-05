<?php

namespace App\Http\Controllers;

use App\Services\AccountsManager;
use App\Services\BotStory;
use App\Services\TimewebState;
use Illuminate\Http\Request;

class FuturesController extends Controller
{
    private function dataDir(): string
    {
        return rtrim((string) env('FUTURES_DATA_DIR', storage_path('app/futures')), '/\\');
    }

    private function fallbackDir(): string
    {
        return base_path('python_research/futures');
    }

    private function readJson(string $name, bool $preferRemote = false): array
    {
        if ($preferRemote) {
            $remote = TimewebState::readJson($name);
            if (!empty($remote)) {
                return $remote;
            }
        }
        foreach ([$this->dataDir(), $this->fallbackDir()] as $dir) {
            $path = $dir . '/' . $name;
            if (file_exists($path)) {
                $d = json_decode((string) file_get_contents($path), true);
                if (is_array($d) && !empty($d)) {
                    return $d;
                }
            }
        }
        return $preferRemote ? [] : TimewebState::readJson($name);
    }

    private function readAccountJson(string $stem, string $accountId): array
    {
        $acc = strtolower(trim($accountId)) ?: 'main';
        $names = ["{$stem}.{$acc}.json"];
        if ($acc === 'main') {
            $names[] = "{$stem}.json";
        }
        // Check runtime dataDir first, then S3, and only then git fallbackDir
        foreach ($names as $name) {
            $runtimePath = $this->dataDir() . '/' . $name;
            if (file_exists($runtimePath)) {
                $d = json_decode((string) file_get_contents($runtimePath), true);
                if (is_array($d) && !empty($d)) {
                    return $d;
                }
            }
        }
        foreach ($names as $name) {
            $remote = TimewebState::readJson($name);
            if (!empty($remote)) {
                return $remote;
            }
        }
        foreach ($names as $name) {
            $fallbackPath = $this->fallbackDir() . '/' . $name;
            if (file_exists($fallbackPath)) {
                $d = json_decode((string) file_get_contents($fallbackPath), true);
                if (is_array($d) && !empty($d)) {
                    return $d;
                }
            }
        }
        return [];
    }

    private function resolveActiveAccount(Request $request): array
    {
        $cfg = AccountsManager::loadConfig();
        $allAccounts = $cfg['accounts'] ?? [];
        $isMaster = (bool) $request->session()->get('is_master', false);
        $pinnedId = (string) $request->session()->get('pin_account_id', 'main');

        if ($isMaster) {
            $visible = $allAccounts;
            $reqId = strtolower(trim((string) $request->query('account', $pinnedId ?: 'main')));
        } else {
            $visible = array_values(array_filter($allAccounts, fn ($a) => ($a['id'] ?? '') === $pinnedId));
            if (!$visible) {
                $visible = [$allAccounts[0]];
            }
            $reqId = $visible[0]['id'];
        }

        $active = $visible[0];
        foreach ($visible as $acc) {
            if (($acc['id'] ?? '') === $reqId) {
                $active = $acc;
                break;
            }
        }

        return [
            'is_master' => $isMaster,
            'active' => $active,
            'visible' => $visible,
            'all' => $allAccounts,
        ];
    }

    private function modelConfig(array $account): array
    {
        $lev = (float) ($account['leverage'] ?? 7.0);
        $slev = (float) ($account['sleeve_leverage'] ?? 3.0);
        $riskPct = round(((float) ($account['risk_pct'] ?? 0.075)) * 100, 2);
        $marginPct = round(((float) ($account['max_margin_frac'] ?? 0.60)) * 100, 0);
        $maxConc = (int) ($account['max_conc'] ?? 2);
        $enSleeve = (bool) ($account['enable_sleeve'] ?? true);

        return [
            'train' => 'BTC (10d EMA фильтр режима) + 7 ликвидных альтов',
            'trade' => 'Ядро: ETH, SOL, ADA, SUI + Ротация (XRP, AVAX, LINK при серии > +1.5%)',
            'window' => '365 дней (каузальный walk-forward только по закрытым сделкам)',
            'threshold' => '10d EMA + 48h EMA пробой + каскадный тейк 3.0 / 5.5 ATR + пирамида',
            'leverage' => "{$lev}x (Risk {$riskPct}%)" . ($enSleeve ? " + Корзина {$slev}x" : ''),
            'stake' => "до {$marginPct}% маржи (макс. {$maxConc} поз. одновременно)",
            'features' => 'Полный объём до одного выхода, пирамида +50% на +2 ATR, авто-ресайз объёмов при пополнении/выводе',
            'retrain' => 'Каждые 5 минут сверка счёта и переводов, каждый час новые входы. Конфиг счетов и состояние хранятся в S3 Timeweb',
            'backtest' => $this->honestBacktest(),
        ];
    }

    private function honestBacktest(): array
    {
        $full = $this->readJson('stable_causal_full.json');
        $year = $this->readJson('stable_recent365.json');
        if (!$full || empty($full['causal'])) {
            return ['note' => 'честный прогон ещё не записан'];
        }
        return [
            'return_pct' => $full['return_pct'] ?? null,
            'max_dd_pct' => $full['max_dd_pct'] ?? null,
            'trades' => $full['taken'] ?? 0,
            'wins' => $full['wins'] ?? 0,
            'win_rate_pct' => $full['win_rate_pct'] ?? 37.9,
            'recent90_pct' => $full['recent90_pct'] ?? null,
            'year_pct' => $year['return_pct'] ?? null,
            'half_years' => $full['half_years'] ?? [],
            'note' => 'Чемпионат Шварца (Ultra 7x): каскадный тейк (TP1 3.0 ATR / TP2 5.5 ATR + 65% Runner), замок пирамиды, быстрый выход fast_bail за 1–4ч.',
        ];
    }

    public function dashboard(Request $request)
    {
        $resolved = $this->resolveActiveAccount($request);
        $active = $resolved['active'];
        $accId = (string) ($active['id'] ?? 'main');

        return view('site.futures', [
            'config' => $this->modelConfig($active),
            'signals' => $this->readAccountJson('futures_signals', $accId),
            'compare' => $this->readAccountJson('futures_compare', $accId),
            'activeAccount' => $active,
            'accounts' => $resolved['visible'],
            'allAccounts' => $resolved['all'],
            'isMaster' => $resolved['is_master'],
        ]);
    }

    public function compare(Request $request)
    {
        $resolved = $this->resolveActiveAccount($request);
        $accId = (string) ($resolved['active']['id'] ?? 'main');
        return view('site.futures_compare', [
            'compare' => $this->readAccountJson('futures_compare', $accId),
        ]);
    }

    public function story(Request $request)
    {
        $resolved = $this->resolveActiveAccount($request);
        $accId = (string) ($resolved['active']['id'] ?? 'main');
        $story = (new BotStory(base_path()))->build(
            $this->readJson('sleeve_compare.json'),
            $this->readAccountJson('futures_compare', $accId)
        );
        return view('site.bot', ['story' => $story]);
    }

    public function saveAccount(Request $request)
    {
        if (!$request->session()->get('is_master', false)) {
            abort(403, 'Только мастер-пин может менять конфигурацию счетов S3');
        }
        $res = AccountsManager::upsertAccount($request->all());
        $accId = $res['id'] ?? 'main';
        return redirect('/?account=' . urlencode($accId))->with(
            'status',
            $res['ok']
                ? "Аккаунт «{$accId}» сохранён в S3. Планировщик подхватит настройки на ближайшем тике без деплоя."
                : "Аккаунт «{$accId}» сохранён локально (проверьте доступ к S3)."
        );
    }

    public function accountAction(Request $request)
    {
        $id = strtolower(trim((string) $request->input('id', '')));
        $action = (string) $request->input('action', 'toggle');
        $isMaster = (bool) $request->session()->get('is_master', false);
        $sessionAcc = strtolower(trim((string) $request->session()->get('account_id', 'main')));

        $isPauseAction = in_array($action, ['toggle_pause', 'pause', 'resume'], true);
        if (!$isMaster && (!$isPauseAction || $id !== $sessionAcc)) {
            abort(403, 'Недостаточно прав для управления этим счётом');
        }
        AccountsManager::accountAction($id, $action);
        $target = $action === 'delete' ? 'main' : ($id ?: 'main');
        $msg = $isPauseAction
            ? "Режим открытия новых сделок для «{$id}» обновлён в S3."
            : "Статус аккаунта «{$id}» обновлён в S3.";
        return redirect('/?account=' . urlencode($target))->with('status', $msg);
    }
}
