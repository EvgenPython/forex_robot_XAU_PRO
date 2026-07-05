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

class CycleStatus(Enum):
    SUCCESS = "SUCCESS"
    MT5_UNAVAILABLE = "MT5_UNAVAILABLE"
    TERMINAL_UNAVAILABLE = "TERMINAL_UNAVAILABLE"
    ACCOUNT_UNAVAILABLE = "ACCOUNT_UNAVAILABLE"
    QUOTES_UNAVAILABLE = "QUOTES_UNAVAILABLE"
    ACCOUNT_CONTEXT_MISMATCH = "ACCOUNT_CONTEXT_MISMATCH"
    STATE_SAFETY_BLOCKED = "STATE_SAFETY_BLOCKED"
    STATE_RECOVERED = "STATE_RECOVERED"
    TRADE_DISABLED = "TRADE_DISABLED"
    ACCOUNT_GUARD_BLOCKED = "ACCOUNT_GUARD_BLOCKED"
    DAILY_HARD_STOP = "DAILY_HARD_STOP"
    DAILY_SOFT_STOP = "DAILY_SOFT_STOP"
    POSITION_MANAGED = "POSITION_MANAGED"
    POSITION_RECONCILING = "POSITION_RECONCILING"
    POSITION_WITHOUT_STATE = "POSITION_WITHOUT_STATE"
    PENDING_OPEN = "PENDING_OPEN"
    PENDING_OPEN_RECOVERED = "PENDING_OPEN_RECOVERED"
    NO_NEW_CANDLE = "NO_NEW_CANDLE"
    WAIT_SIGNAL = "WAIT_SIGNAL"
    TRADE_OPENED = "TRADE_OPENED"
    SIGNAL_REJECTED = "SIGNAL_REJECTED"
    EXECUTION_ANOMALY = "EXECUTION_ANOMALY"


@dataclass
class CycleResult:
    status: CycleStatus
    healthy: bool = True
    mt5_available: bool = True
    terminal_available: bool = True
    account_available: bool = True
    quotes_available: bool = True
    trade_allowed: bool = True
    mt5_issue_code: Optional[str] = None
    message: str = ""
    account_login: Optional[int] = None
    server: Optional[str] = None

    @property
    def status_value(self) -> str:
        return self.status.value

