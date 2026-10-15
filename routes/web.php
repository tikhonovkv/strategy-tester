<?php

use App\Http\Controllers\SiteController;
use Illuminate\Support\Facades\Route;

Route::get('/', [SiteController::class, 'index'])->name('home');
Route::get('/autonomous', [SiteController::class, 'autonomous'])->name('autonomous');
Route::post('/autonomous/settings', [SiteController::class, 'saveSettings'])->name('autonomous.settings');
Route::post('/autonomous/run-now', [SiteController::class, 'runNow'])->name('autonomous.run');
Route::get('/variants', [SiteController::class, 'variants'])->name('variants');
Route::get('/scanner', [SiteController::class, 'scanner'])->name('scanner');
Route::get('/about', [SiteController::class, 'about'])->name('about');
Route::get('/api/scan/{symbol?}', [SiteController::class, 'apiScan'])->name('api.scan');
Route::get('/api/cron/hourly', [SiteController::class, 'apiHourlyCron'])->name('api.cron.hourly');
