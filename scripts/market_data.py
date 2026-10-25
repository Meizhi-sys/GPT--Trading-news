"""Bounded, best-effort Yahoo market-data collection using the standard library.

This module never treats Yahoo's finite gainers sample as a whole-market scan.
Daily historical prices are unadjusted; futures are individual front-contract
quotes and must not be presented as spot prices or continuous-contract returns.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import math
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


USER_AGENT = "TradingNewsReport/1.0 (personal research; public market data)"
REQUEST_TIMEOUT = 20
MAX_RETRIES = 2
MAX_AGE = timedelta(days=5)
FUTURE_TOLERANCE = timedelta(minutes=5)
NEW_YORK = ZoneInfo("America/New_York")
EQUITY_SYMBOLS = ("MU", "MRNA", "SPY", "QQQ", "SOXX")
COMMODITY_SYMBOLS = ("GC=F", "BZ=F")
ACCEPTED_EXCHANGES = frozenset(("NYQ", "NMS", "NGM", "NCM", "ASE"))
GAINERS_URL = (
    "https://query1.finance.yahoo.com/v1/finance/screener/predefined/saved?"
    + urlencode({"scrIds": "day_gainers", "count": 250})
)
GAINERS_SCOPE = "Yahoo 当日涨幅榜最多 250 个样本中符合条件的前 10"
FUTURES_WARNING = (
    "期货报价，非现货；所示代码可能随近月合约切换，"
    "换月前后不具备连续收益可比性，不据此计算周收益或连续趋势。"
)


class DataUnavailable(ValueError):
    """An expected source or validation failure, safe to show without secrets."""


def _number(value: Any, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        raise DataUnavailable("缺少有效数值")
    result = float(value)
    if not math.isfinite(result) or (positive and result <= 0):
        raise DataUnavailable("数值非有限或价格不大于零")
    return result


def _volume(value: Any) -> int:
    result = _number(value)
    if result < 0 or result != int(result):
        raise DataUnavailable("成交量必须为非负整数")
    return int(result)


def _timestamp(value: Any, now: datetime) -> datetime:
    try:
        result = datetime.fromtimestamp(_number(value, positive=True), timezone.utc)
    except (OverflowError, OSError, ValueError) as exc:
        raise DataUnavailable("无效行情时间") from exc
    if result > now + FUTURE_TOLERANCE:
        raise DataUnavailable("行情时间晚于报告截止时间")
    if now - result > MAX_AGE:
        raise DataUnavailable("行情超过五天未更新，拒绝当作当前行情")
    return result


def _change(price: Any, previous_close: Any) -> float:
    latest = _number(price, positive=True)
    previous = _number(previous_close, positive=True)
    return (latest / previous - 1.0) * 100.0


def _request_json(url: str) -> dict[str, Any]:
    """Verify TLS normally, retry only transient failures, never print bodies."""
    for attempt in range(MAX_RETRIES + 1):
        try:
            request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
            with urlopen(request, timeout=REQUEST_TIMEOUT) as response:
                payload = json.load(response)
            if not isinstance(payload, dict):
                raise DataUnavailable("数据源返回了非对象 JSON")
            return payload
        except HTTPError as exc:
            if exc.code not in (429, 500, 502, 503, 504) or attempt == MAX_RETRIES:
                raise DataUnavailable(f"数据源 HTTP {exc.code}") from None
        except (URLError, TimeoutError, OSError):
            if attempt == MAX_RETRIES:
                raise DataUnavailable("数据源连接失败或超时") from None
        except (json.JSONDecodeError, UnicodeError):
            raise DataUnavailable("数据源不是有效 JSON") from None
        time.sleep(0.5 * (attempt + 1))
    raise DataUnavailable("数据源请求失败")


def _chart_url(symbol: str) -> str:
    return (
        "https://query1.finance.yahoo.com/v8/finance/chart/"
        + quote(symbol, safe="")
        + "?interval=1d&range=3mo&includePrePost=false"
    )


def _read_chart(payload: dict[str, Any], symbol: str, now: datetime) -> dict[str, Any]:
    try:
        chart = payload["chart"]
        if chart.get("error"):
            raise DataUnavailable("行情接口报告错误")
        result = chart["result"][0]
        meta = result["meta"]
        if meta.get("symbol") != symbol:
            raise DataUnavailable("返回的股票代码不匹配")
        if meta.get("currency") != "USD":
            raise DataUnavailable("非美元或币种缺失，不能作为美元报价")
        price = _number(meta.get("regularMarketPrice"), positive=True)
        market_time = _timestamp(meta.get("regularMarketTime"), now)
        zone = ZoneInfo(meta["exchangeTimezoneName"])
        market_date = market_time.astimezone(zone).date()
        stamps = result.get("timestamp", [])
        bars = result["indicators"]["quote"][0]
        closes = bars.get("close", [])
        volumes = bars.get("volume", [])
        if len(stamps) != len(closes) or len(stamps) != len(volumes):
            raise DataUnavailable("历史行情数组长度不一致")
        history = []
        for raw_time, raw_close, raw_volume in zip(stamps, closes, volumes):
            # Empty trading-day bars occur at source; do not fabricate them.
            if raw_close is None or raw_volume is None:
                continue
            stamp = datetime.fromtimestamp(_number(raw_time, positive=True), timezone.utc)
            if stamp > now + FUTURE_TOLERANCE:
                raise DataUnavailable("历史行情包含未来日期")
            history.append({
                "date": stamp.astimezone(zone).date().isoformat(),
                "close": _number(raw_close, positive=True),
                "volume": _volume(raw_volume),
            })
        history.sort(key=lambda item: item["date"])
        if len({item["date"] for item in history}) != len(history):
            raise DataUnavailable("历史行情包含重复交易日期")
        if not history or history[-1]["date"] != market_date.isoformat():
            raise DataUnavailable("最新历史日线与行情日期不一致")
        previous_bars = [item for item in history if item["date"] < market_date.isoformat()]
        # chartPreviousClose/previousClose can refer to the start of a 3-month
        # query. Never use either as the previous session's close.
        if meta.get("regularMarketPreviousClose") is not None:
            previous = _number(meta["regularMarketPreviousClose"], positive=True)
            previous_method = "regularMarketPreviousClose"
        elif previous_bars:
            previous = previous_bars[-1]["close"]
            previous_method = "同一接口最近前一交易日的未复权收盘价"
        else:
            raise DataUnavailable("缺少可核实的前一交易日收盘价")
        volume = _volume(meta.get("regularMarketVolume", history[-1]["volume"]))
        record = {
            "symbol": symbol,
            "price": price,
            "previous_close": previous,
            "change_percent": _change(price, previous),
            "volume": volume,
            "market_time": market_time.isoformat(),
            "market_date": market_date.isoformat(),
            "source_url": _chart_url(symbol),
            "currency": meta.get("currency"),
            "exchange_timezone": meta["exchangeTimezoneName"],
            "price_basis": "常规时段最新报价；历史日线未复权，非实时性承诺",
            "previous_close_method": previous_method,
            "history": history,
            "limitations": ["未复权历史价格可能受拆股、分红等公司行动影响；需核查后判断长期趋势。"],
        }
        if symbol in COMMODITY_SYMBOLS:
            record["instrument_type"] = "futures"
            record["label"] = "黄金期货" if symbol == "GC=F" else "布伦特原油期货"
            record["unit"] = "美元/金衡盎司" if symbol == "GC=F" else "美元/桶"
            record["limitations"].append(FUTURES_WARNING)
            record["contract_month"] = None
            record["limitations"].append("该连续代码未提供已核实的具体合约月份，不作为特定月份合约结算价。")
        return record
    except DataUnavailable:
        raise
    except (KeyError, IndexError, TypeError, AttributeError, ValueError, OverflowError, OSError):
        raise DataUnavailable("行情响应缺少必要字段或字段格式不正确") from None


def _read_gainers(payload: dict[str, Any], now: datetime) -> dict[str, Any]:
    data: dict[str, Any] = {
        "scope": GAINERS_SCOPE,
        "filters": {
            "minimum_price_usd": 10,
            "quote_type": "EQUITY",
            "exchanges": sorted(ACCEPTED_EXCHANGES),
            "session": "regular",
        },
        "rows": [],
        "limitations": [
            "仅为 Yahoo day_gainers 有限样本，不能称为过滤后的全市场前 10。",
            "EQUITY 分类包含普通股和 ADR，但不能完全识别优先股等特殊证券；需核查证券类型。",
            "公司行动可能造成异常涨幅；未核实上涨催化时不推断基本面或机构操纵。",
        ],
    }
    try:
        finance = payload["finance"]
        if finance.get("error"):
            raise DataUnavailable("涨幅榜接口报告错误")
        results = finance["result"]
        if not isinstance(results, list) or not results:
            raise DataUnavailable("涨幅榜响应缺少结果")
        quotes = results[0]["quotes"]
        if not isinstance(quotes, list):
            raise DataUnavailable("涨幅榜不是列表")
        rejected = 0
        for item in quotes[:250]:
            if not isinstance(item, dict):
                rejected += 1
                continue
            if item.get("quoteType") != "EQUITY" or item.get("exchange") not in ACCEPTED_EXCHANGES:
                continue
            try:
                price = _number(item.get("regularMarketPrice"), positive=True)
                if price < 10:
                    continue
                if item.get("currency") != "USD":
                    raise DataUnavailable("非美元或币种缺失")
                previous = _number(item.get("regularMarketPreviousClose"), positive=True)
                market_time = _timestamp(item.get("regularMarketTime"), now)
                symbol = item.get("symbol")
                if not isinstance(symbol, str) or not symbol.strip():
                    raise DataUnavailable("代码缺失")
                volume = _volume(item.get("regularMarketVolume"))
                change_percent = _change(price, previous)
                if change_percent <= 0:
                    continue
                market_cap = item.get("marketCap")
                if market_cap is not None:
                    market_cap = _number(market_cap, positive=True)
                data["rows"].append({
                    "symbol": symbol,
                    "name": item.get("longName") or item.get("shortName") or symbol,
                    "price": price,
                    "previous_close": previous,
                    "change_percent": change_percent,
                    "volume": volume,
                    "market_time": market_time.isoformat(),
                    "market_date": market_time.astimezone(NEW_YORK).date().isoformat(),
                    "source_url": GAINERS_URL,
                    "currency": "USD",
                    "exchange": item["exchange"],
                    "market_cap": market_cap,
                    "estimated_turnover_usd": price * volume,
                    "turnover_basis": "收盘/最新常规报价乘以成交量的估算，并非实际逐笔成交额",
                    "catalyst": "未核实；不因涨幅推断上涨原因",
                    "security_type": "Yahoo EQUITY 分类，普通股/ADR 身份仍需单独核实",
                })
            except DataUnavailable:
                rejected += 1
        # Never mix two sessions into a single daily ranking.
        dates = {row["market_date"] for row in data["rows"]}
        if len(dates) > 1:
            latest_date = max(dates)
            data["rows"] = [row for row in data["rows"] if row["market_date"] == latest_date]
            data["limitations"].append("样本包含不同交易日期，已排除较旧日期，榜单可能不完整。")
        data["rows"].sort(key=lambda row: (-row["change_percent"], row["symbol"]))
        # Duplicate source entries must not occupy two ranks.
        seen: set[str] = set()
        distinct_rows = []
        for row in data["rows"]:
            if row["symbol"] not in seen:
                distinct_rows.append(row)
                seen.add(row["symbol"])
        data["rows"] = distinct_rows[:10]
        if rejected:
            data["limitations"].append(f"有 {rejected} 条样本因字段缺失、无效或时间异常被排除。")
        if len(data["rows"]) < 10:
            data["limitations"].append(f"仅核实到 {len(data['rows'])} 条符合条件的记录，不补造前 10。")
        return data
    except DataUnavailable:
        raise
    except (KeyError, IndexError, TypeError, AttributeError, ValueError):
        raise DataUnavailable("涨幅榜响应缺少必要字段或字段格式不正确") from None


def collect_market_data(now: datetime) -> dict[str, Any]:
    """Return verified records and explicit per-source errors; never invent data.

    ``now`` must be timezone-aware and is the report's data cutoff. Failures in
    one symbol do not prevent independent symbols or the gainers scan running.
    """
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be a timezone-aware datetime")
    now = now.astimezone(timezone.utc)
    result: dict[str, Any] = {
        "as_of": now.isoformat(),
        "equities": [],
        "commodities": [],
        "gainers": {
            "scope": GAINERS_SCOPE,
            "filters": {"minimum_price_usd": 10, "quote_type": "EQUITY", "exchanges": sorted(ACCEPTED_EXCHANGES)},
            "rows": [],
            "limitations": [],
        },
        "errors": [],
    }
    for symbol in EQUITY_SYMBOLS + COMMODITY_SYMBOLS:
        try:
            record = _read_chart(_request_json(_chart_url(symbol)), symbol, now)
            target = "commodities" if symbol in COMMODITY_SYMBOLS else "equities"
            result[target].append(record)
        except DataUnavailable as exc:
            result["errors"].append(f"{symbol}：{exc}；来源 {_chart_url(symbol)}")
    try:
        result["gainers"] = _read_gainers(_request_json(GAINERS_URL), now)
        if not result["gainers"]["rows"]:
            result["errors"].append(f"涨幅榜：无可核实的符合条件记录；来源 {GAINERS_URL}")
    except DataUnavailable as exc:
        result["gainers"]["limitations"].append("涨幅榜来源不可用；没有生成行情排名。")
        result["errors"].append(f"涨幅榜：{exc}；来源 {GAINERS_URL}")
    return result
