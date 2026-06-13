import MetaTrader5 as mt5


def prepare_order(
    symbol: str,
    direction: str,
    volume: float,
    stop_loss: float,
    comment: str = "mt5_xau_bot",
):
    tick = mt5.symbol_info_tick(symbol)

    if tick is None:
        raise RuntimeError(f"Cannot get tick for {symbol}: {mt5.last_error()}")

    if direction == "BUY":
        order_type = mt5.ORDER_TYPE_BUY
        price = tick.ask
    elif direction == "SELL":
        order_type = mt5.ORDER_TYPE_SELL
        price = tick.bid
    else:
        raise ValueError(f"Unsupported direction: {direction}")

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": float(volume),
        "type": order_type,
        "price": float(price),
        "sl": float(stop_loss),
        "deviation": 30,
        "magic": 260611,
        "comment": comment,
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }

    return request


def send_market_order(
    symbol: str,
    direction: str,
    volume: float,
    stop_loss: float,
    dry_run: bool = True,
):
    request = prepare_order(
        symbol=symbol,
        direction=direction,
        volume=volume,
        stop_loss=stop_loss,
    )

    print("=" * 50)
    print("ORDER PREPARED")
    print(f"symbol: {symbol}")
    print(f"direction: {direction}")
    print(f"volume: {volume}")
    print(f"price: {request['price']}")
    print(f"stop_loss: {stop_loss}")
    print(f"dry_run: {dry_run}")

    if dry_run:
        print("DRY RUN: order was NOT sent")
        print("=" * 50)
        return {
            "sent": False,
            "dry_run": True,
            "request": request,
            "result": None,
        }

    result = mt5.order_send(request)

    print("ORDER SEND RESULT")
    print(result)
    print("=" * 50)

    if result is None:
        raise RuntimeError(f"order_send returned None: {mt5.last_error()}")

    if result.retcode != mt5.TRADE_RETCODE_DONE:
        raise RuntimeError(f"Order failed: retcode={result.retcode}, comment={result.comment}")

    return {
        "sent": True,
        "dry_run": False,
        "request": request,
        "result": result,
    }