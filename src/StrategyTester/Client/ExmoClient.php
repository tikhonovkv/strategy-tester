<?php

namespace StrategyTester\Client;

class ExmoClient implements OrderInterface, UserBalanceInteface
{
    protected $key;

    public function __construct(string $api_key, string $secret)
    {
        $this->key = $api_key;
    }

    public function getKey()
    {
        return $this->key;
    }

    public function opened(): array
    {
        // TODO: Implement opened() method.
    }

    public function createOrder($price, $volume)
    {
        // TODO: Implement createOrder() method.
    }

    public function closeOrder($price, $volume)
    {
        // TODO: Implement closeOrder() method.
    }
}