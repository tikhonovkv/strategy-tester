<?php

use PHPUnit\Framework\TestCase;
use StrategyTester\Client\ExmoClient;

class InitTest extends TestCase
{
    public function testInit()
    {
        $key = 'dawd1312e21321';
        $client = $this->getMockBuilder(ExmoClient::class)->setConstructorArgs([
            'api_key' => $key,
            'secret' => '124512512dfawdaw'
        ])->onlyMethods(['opened', 'createOrder'])->getMock();

        $this->assertEquals($key, $client->getKey());
    }
}