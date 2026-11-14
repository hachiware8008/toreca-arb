"""利益計算・照合・再通知抑制・死活監視の判定（通信なしの純粋な処理）。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from . import config as C

JST = timezone(timedelta(hours=9))


def profit(a_price: int, b_price: int) -> int:
    return b_price - a_price - C.A_SHIPPING - C.B_SHIPPING - C.TRANSFER_FEE


def is_opportunity(a_price: int, b_price: int) -> bool:
    if a_price < C.MIN_A_PRICE:
        return False
    p = profit(a_price, b_price)
    return p >= C.MIN_PROFIT and p / a_price >= C.MIN_PROFIT_RATE


def key_str(k: dict) -> str:
    return f"{k['set_code']}|{k['number']}|{k['rarity']}|{k['name']}|{k['grade']}"


def find_opportunities(a_records: dict, b_records: dict, now: datetime) -> list[dict]:
    """a_records / b_records: pid -> record（key, price, url, seen を持つ辞書）。"""
    b_by_key: dict[str, dict] = {}
    for r in b_records.values():
        ks = key_str(r["key"])
        if ks not in b_by_key or r["price"] > b_by_key[ks]["price"]:
            b_by_key[ks] = r
    out = []
    for a in a_records.values():
        if not a.get("in_stock"):
            continue
        if now - datetime.fromisoformat(a["seen"]) > timedelta(hours=C.A_STALE_HOURS):
            continue
        b = b_by_key.get(key_str(a["key"]))
        if not b or not is_opportunity(a["price"], b["price"]):
            continue
        out.append({
            "pair": f"cardrush:{a['pid']}>hare2buy:{b['pid']}",
            "a": a, "b": b,
            "profit": profit(a["price"], b["price"]),
        })
    out.sort(key=lambda o: o["profit"], reverse=True)
    return out


def should_notify(pair: str, notified: dict, now: datetime) -> bool:
    last = notified.get(pair)
    return last is None or now - datetime.fromisoformat(last) >= timedelta(hours=C.RENOTIFY_HOURS)


def heartbeat_due(state: dict, now: datetime) -> bool:
    jst = now.astimezone(JST)
    return jst.hour >= C.HEARTBEAT_HOUR_JST and state.get("last_heartbeat_date") != jst.date().isoformat()


def update_failures(state: dict, shop: str, ok: bool) -> bool:
    """失敗回数を更新し、ちょうど閾値に達したとき True（=今すぐ通知）を返す。"""
    fails = state.setdefault("consecutive_failures", {})
    if ok:
        fails[shop] = 0
        return False
    fails[shop] = fails.get(shop, 0) + 1
    return fails[shop] == C.FAIL_ALERT_THRESHOLD


def prune(records: dict, now: datetime) -> dict:
    limit = timedelta(hours=C.DROP_AFTER_HOURS)
    return {k: v for k, v in records.items() if now - datetime.fromisoformat(v["seen"]) <= limit}
