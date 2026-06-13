from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional


class SignalAction(Enum):
    BUY = "BUY"
    SELL = "SELL"
    WAIT = "WAIT"


@dataclass
class Signal:
    action: SignalAction
    score: int
    reasons: list[str]

    entry_price: float = 0.0

    stop_loss: Optional[float] = None

    tp1: Optional[float] = None
    tp2: Optional[float] = None
    tp3: Optional[float] = None

    atr: Optional[float] = None

    buy_score: int = 0
    sell_score: int = 0


@dataclass
class Trade:
    side: str

    entry_time: datetime
    exit_time: Optional[datetime]

    entry_price: float
    exit_price: Optional[float]

    stop_loss: float
    take_profit: float

    pnl: float = 0.0
    rr: float = 0.0

    close_reason: str = ""

    is_closed: bool = False
    moved_to_breakeven: bool = False

    risk_percent: float = 1.0

    highest_price: float = 0.0
    lowest_price: float = 0.0

    candles_in_trade: int = 0

    def update_extremes(self, high: float, low: float) -> None:
        if self.highest_price == 0.0:
            self.highest_price = high
        else:
            self.highest_price = max(self.highest_price, high)

        if self.lowest_price == 0.0:
            self.lowest_price = low
        else:
            self.lowest_price = min(self.lowest_price, low)

    def increment_candles(self) -> None:
        self.candles_in_trade += 1

    def close(
        self,
        exit_time: datetime,
        exit_price: float,
        pnl: float,
        rr: float,
        close_reason: str,
    ) -> None:
        self.exit_time = exit_time
        self.exit_price = exit_price
        self.pnl = pnl
        self.rr = rr
        self.close_reason = close_reason
        self.is_closed = True