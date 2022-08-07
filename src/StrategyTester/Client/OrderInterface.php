<?php

namespace StrategyTester\Client;

interface OrderInterface
{
    public function opened(): array;
    public function createOrder($price, $volume);
    public function closeOrder($price, $volume);
}