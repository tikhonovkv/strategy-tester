<?php

use App\Http\Controllers\SiteController;
use Illuminate\Support\Facades\Route;

Route::get('/', [SiteController::class, 'index'])->name('home');
Route::get('/variants', [SiteController::class, 'variants'])->name('variants');
Route::get('/scanner', [SiteController::class, 'scanner'])->name('scanner');
Route::get('/about', [SiteController::class, 'about'])->name('about');
Route::get('/api/scan/{symbol?}', [SiteController::class, 'apiScan'])->name('api.scan');
