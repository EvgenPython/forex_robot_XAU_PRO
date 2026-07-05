"""Безопасное русское отображение внутренних кодов и сообщений.

Внутренние значения BUY/SELL, коды MT5, CycleStatus и reason_code не меняются,
чтобы не нарушить state, историю и защитную логику. Этот модуль переводит их
только при выводе пользователю, в Telegram, консоль и журналы.
"""

from __future__ import annotations


_SIGNAL_REASON_EXACT = {
    "H4 bullish context": "H4: восходящий контекст",
    "H4 bearish context": "H4: нисходящий контекст",
    "H1 bullish trend": "H1: восходящий тренд",
    "H1 bearish trend": "H1: нисходящий тренд",
    "H1 bullish structure": "H1: восходящая структура",
    "H1 bearish structure": "H1: нисходящая структура",
    "M15 bullish timing": "M15: подтверждение покупки",
    "M15 bearish timing": "M15: подтверждение продажи",
    "Distance filter OK": "Фильтр расстояния пройден",
    "No dominant direction": "Нет преобладающего направления",
    "No swing low": "Не найден подтверждённый минимум",
    "No swing high": "Не найден подтверждённый максимум",
    "Stop distance filter blocked BUY": "Покупка отклонена фильтром расстояния до стоп-лосса",
    "Stop distance filter blocked SELL": "Продажа отклонена фильтром расстояния до стоп-лосса",
}

_SAFETY_REASON_CODES = {
    "POSITION_WITHOUT_STATE": "В MT5 есть позиция, отсутствующая в состоянии робота",
    "STATE_FILE_CORRUPTED": "Файл состояния повреждён или недоступен",
    "STATE_CORRUPTED": "Файл состояния повреждён или недоступен",
    "PENDING_ACCOUNT_MISMATCH": "Ожидающее открытие относится к другому счёту",
    "ACCOUNT_CONTEXT_MISMATCH": "Состояние относится к другому счёту",
    "ORDER_SEND_RESULT_UNKNOWN": "Результат отправки ордера не удалось однозначно определить",
    "PARTIAL_OPEN_EXECUTION": "MT5 сообщил о неполном исполнении открытия",
    "PARTIAL_CLOSE_EXECUTION": "MT5 сообщил о неполном исполнении закрытия",
    "PARTIAL_SL_EXECUTION": "MT5 сообщил о неполном исполнении изменения стоп-лосса",
    "POSITION_INTEGRITY_MISMATCH": "Параметры позиции не совпадают с состоянием робота",
    "MULTIPLE_CLOSING_DEALS": "В истории обнаружено неожиданное количество закрывающих сделок",
    "CLOSE_VOLUME_MISMATCH": "Объём закрывающей сделки не совпадает с объёмом позиции",
    "CLOSE_EXECUTED_VOLUME_MISMATCH": "MT5 сообщил неожиданный исполненный объём при закрытии",
    "CLOSE_DEAL_VOLUME_MISMATCH": "Объём закрывающей торговой операции не совпадает с объёмом позиции",
    "STATE_SAFETY_BLOCK": "Сработала защита состояния робота",
}

_MT5_ISSUE_CODES = {
    "TERMINAL_UNAVAILABLE": "Терминал MT5 недоступен",
    "ACCOUNT_UNAVAILABLE": "Данные торгового счёта недоступны",
    "QUOTES_UNAVAILABLE": "Котировки недоступны",
    "TRADE_DISABLED": "Автоматическая торговля отключена",
}


_RUNTIME_STATUS = {
    "NONE": "Нет действий",
    "BLOCKED": "Действия заблокированы защитой",
    "ACTIVATED": "Позиция восстановлена и активирована",
    "CLEARED_NO_EVIDENCE": "Ожидающее открытие удалено: исполнения не найдено",
    "PENDING": "Ожидается подтверждение открытия",
    "POSITION_ANOMALY": "Обнаружено несоответствие параметров позиции",
    "OPEN": "Позиция открыта и подтверждена",
    "WAITING_HISTORY": "Ожидается появление закрытия в истории MT5",
    "CLOSE_HISTORY_ANOMALY": "Обнаружено несоответствие в истории закрытия",
    "CLOSED_ALREADY_PROCESSED": "Закрытие уже было обработано",
    "NOTIFICATION_PENDING": "Ожидается повторная отправка уведомления",
    "CLOSED": "Закрытие подтверждено",
    "NO_STATE": "Состояние активной сделки отсутствует",
    "POSITION_MISSING": "Позиция временно не найдена в MT5",
    "CLOSE_RETRY": "Повторно проверяется запрос на полное закрытие",
    "PRICE_UNAVAILABLE": "Текущая котировка недоступна",
    "CLOSE_REQUESTED": "Отправлен запрос на полное закрытие",
    "MANAGED": "Открытая позиция сопровождается",
}

_RETCODE_NAMES = {
    10004: "Цена изменилась, требуется повторная котировка",
    10006: "Заявка отклонена",
    10007: "Заявка отменена пользователем",
    10008: "Заявка размещена, итог исполнения ещё не подтверждён",
    10009: "Операция выполнена",
    10010: "Операция выполнена не полностью",
    10011: "Ошибка обработки заявки",
    10012: "Истекло время ожидания ответа MT5",
    10013: "Некорректная заявка",
    10014: "Некорректный объём",
    10015: "Некорректная цена",
    10016: "Некорректный уровень стоп-лосса или тейк-профита",
    10017: "Торговля запрещена",
    10018: "Рынок закрыт",
    10019: "Недостаточно средств",
    10020: "Цена изменилась",
    10021: "Нет актуальных котировок для обработки заявки",
    10024: "Слишком много запросов",
    10027: "Автоматическая торговля отключена в терминале",
    10028: "Заявка заблокирована для обработки",
    10029: "Заявка или позиция заблокирована",
    10030: "Тип исполнения заявки не поддерживается",
    10031: "Нет соединения с торговым сервером",
    10032: "Операция разрешена только для реального счёта",
    10033: "Достигнут лимит отложенных ордеров",
    10034: "Достигнут лимит объёма по инструменту",
    10035: "Некорректный или запрещённый тип ордера",
    10036: "Позиция уже закрыта",
}


def translate_direction(value: str) -> str:
    normalized = str(value or "").upper()
    if normalized == "BUY":
        return "ПОКУПКА"
    if normalized == "SELL":
        return "ПРОДАЖА"
    if normalized == "WAIT":
        return "ОЖИДАНИЕ"
    return normalized or "НЕИЗВЕСТНО"


def translate_mode(server: str | None) -> str:
    text = str(server or "").lower()
    if not text:
        return "⚪ НЕИЗВЕСТНО"
    if "demo" in text:
        return "🟡 ДЕМО"
    return "🟢 РЕАЛЬНЫЙ СЧЁТ"


def translate_stop_type(value: str | None) -> str:
    normalized = str(value or "").upper()
    if normalized == "HARD STOP":
        return "ЖЁСТКАЯ ДНЕВНАЯ БЛОКИРОВКА"
    if normalized == "SOFT STOP":
        return "МЯГКАЯ ДНЕВНАЯ БЛОКИРОВКА"
    return normalized or "НЕ УКАЗАНО"


def translate_signal_reason(value: object) -> str:
    text = str(value or "").strip()
    if text in _SIGNAL_REASON_EXACT:
        return _SIGNAL_REASON_EXACT[text]

    replacements = (
        ("BUY score=", "Оценка покупки="),
        ("SELL score=", "Оценка продажи="),
        ("Minimum score not reached:", "Минимальная оценка не достигнута:"),
        ("Max score exceeded:", "Превышена максимальная оценка:"),
    )
    for source, target in replacements:
        if text.startswith(source):
            return target + text[len(source):]
    return text


def translate_signal_reasons(values: object) -> list[str]:
    if isinstance(values, list):
        return [translate_signal_reason(value) for value in values]
    if values in (None, ""):
        return []
    return [translate_signal_reason(values)]


def translate_safety_reason(value: str | None) -> str:
    code = str(value or "").strip()
    return _SAFETY_REASON_CODES.get(code, code or "—")


def translate_mt5_issue(value: str | None) -> str:
    code = str(value or "").strip()
    return _MT5_ISSUE_CODES.get(code, code or "—")


def translate_retcode(retcode: int, comment: str | None = None) -> str:
    code = int(retcode or 0)
    description = _RETCODE_NAMES.get(code, "Неизвестный ответ MT5")
    comment_text = str(comment or "").strip()
    if comment_text:
        return f"{description} (код {code}, комментарий MT5: {comment_text})"
    return f"{description} (код {code})"


def translate_runtime_status(value: str | None) -> str:
    code = str(value or "").strip()
    return _RUNTIME_STATUS.get(code, code or "—")
