<?php

use App\Http\Controllers\FuturesController;
use App\Http\Controllers\SiteController;
use App\Services\AccountsManager;
use Illuminate\Http\Request;
use Illuminate\Support\Facades\Route;

Route::post('/pin', function (Request $request) {
    $pin = (string) $request->input('pin', '');
    $auth = AccountsManager::verifyPin($pin);
    if ($auth !== null) {
        $request->session()->put('pin_ok', true);
        $request->session()->put('is_master', (bool) $auth['is_master']);
        $request->session()->put('pin_account_id', (string) $auth['account_id']);
        return redirect('/?account=' . urlencode((string) $auth['account_id']));
    }
    $request->session()->flash('pin_bad', true);
    return redirect('/');
});

Route::post('/logout', function (Request $request) {
    $request->session()->forget(['pin_ok', 'is_master', 'pin_account_id']);
    return redirect('/');
})->name('logout');

Route::get('/', [FuturesController::class, 'dashboard'])->name('home');
Route::get('/futures', [FuturesController::class, 'dashboard'])->name('futures');
Route::get('/bot', [FuturesController::class, 'story'])->name('bot');
Route::get('/futures/compare', [FuturesController::class, 'compare'])->name('futures.compare');
Route::post('/accounts/save', [FuturesController::class, 'saveAccount'])->name('accounts.save');
Route::post('/accounts/action', [FuturesController::class, 'accountAction'])->name('accounts.action');

Route::get('/autonomous', [SiteController::class, 'autonomous'])->name('autonomous');
Route::post('/autonomous/settings', [SiteController::class, 'saveSettings'])->name('autonomous.settings');
Route::post('/autonomous/run-now', [SiteController::class, 'runNow'])->name('autonomous.run');
Route::get('/variants', [SiteController::class, 'variants'])->name('variants');
Route::get('/scanner', [SiteController::class, 'scanner'])->name('scanner');
Route::get('/about', [SiteController::class, 'about'])->name('about');
Route::get('/api/scan/{symbol?}', [SiteController::class, 'apiScan'])->name('api.scan');
Route::get('/api/cron/hourly', [SiteController::class, 'apiHourlyCron'])->name('api.cron.hourly');
