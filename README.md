# MT5 XAUUSD Trading Bot

## Overview

This project is a custom-built automated trading system developed for MetaTrader 5 using Python.

The bot is designed specifically for trading XAUUSD (Gold) and operates based on a rule-based strategy combining trend analysis, market structure evaluation, momentum confirmation, and risk management.

The system was developed independently and is not based on any commercial Expert Advisor, signal provider, copy trading service, or third-party automated trading solution.

## Development

The project was developed in Python and integrates with MetaTrader 5 through the official MetaTrader5 Python package.

The development process included:

* strategy research and testing;
* historical backtesting;
* risk management validation;
* demo account forward testing;
* monitoring and logging improvements;
* Telegram notification integration.

All source code, configuration files, logs, and version history belong to the project owner.

## Trading Logic

The trading system uses a multi-timeframe approach:

### Market Context

* H4 trend analysis
* EMA50 and EMA200 trend direction

### Trend Confirmation

* H1 trend validation
* Market structure analysis
* ATR-based volatility assessment

### Entry Timing

* M15 execution timeframe
* EMA50 positioning
* MACD confirmation
* Distance filter from moving averages

Trades are executed only when all required conditions are satisfied.

## Risk Management

The system applies fixed percentage risk per trade.

Risk controls include:

* predefined stop-loss on every position;
* position sizing based on account risk;
* maximum daily drawdown protection;
* automatic trade blocking after daily loss limit;
* break-even protection after predefined profit level;
* single-position management.

No martingale, grid trading, averaging, or recovery systems are used.

## Trade Management

Every position includes:

* stop-loss;
* dynamic position sizing;
* break-even management;
* signal-based exit logic.

The system does not use latency arbitrage, high-frequency trading, tick scalping, news exploitation, or any market manipulation techniques.

## Compliance

The trading system is designed to comply with proprietary trading firm requirements.

The bot:

* trades a single account;
* does not copy trades from external sources;
* does not mirror signals;
* does not use arbitrage strategies;
* does not hedge across accounts;
* does not exploit platform latency;
* does not use HFT execution methods.

All trading decisions are generated internally by the strategy rules.

## Ownership

This software is an original proprietary project developed and maintained by its owner.

The source code, architecture, trading logic, and risk management modules are privately developed and controlled by the owner.

Version control history, source files, development environment, and project documentation can be provided as proof of ownership if required.
