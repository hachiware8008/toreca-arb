"""1回分の監視処理。GitHub Actionsから30分おきに実行される。

1. 晴れる屋2ネット買取の買取価格（高い順の上位ページ）を取得
2. カードラッシュの販売価格（高い順の全件一覧）を前回の続きから数ページ取得
3. 同じカードで「A販売価格 < B買取価格 − 経費」になっていれば ntfy に通知
4. 毎朝の稼働報告と、連続失敗時の即時エラー通知
状態は DATA_DIR（data ブランチ）に JSON / CSV で保存する。
"""
from __future__ import annotations

import csv
import json
import os
import sys
import time
import urllib.request
from dataclasses import asdict
from datetime import datetime, timedelta, timezone

from . import config as C
from .core import (JST, find_opportunities, heartbeat_due, prune, should_notify,
                   update_failures)
from .parsers import cardrush_key, hare2buy_key, in_stock, parse_list

DATA_DIR = os.environ.get("DATA_DIR", "data")


# ---------------------------------------------------------------- I/O helpers
def load(name: str, default):
    path = os.path.join(DATA_DIR, name)
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save(name: str, obj) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(os.path.join(DATA_DIR, name), "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)


def http_get(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": C.USER_AGENT, "Accept-Language": "ja"})
    with urllib.request.urlopen(req, timeout=C.REQUEST_TIMEOUT_SEC) as r:
        return r.read().decode("utf-8", errors="ignore")


def notify(title: str, message: str, click: str | None = None, actions: list | None = None,
           priority: int = 3) -> None:
    topic = os.environ.get("NTFY_TOPIC")
    if not topic:
        print(f"[notify skipped] {title}: {message}")
        return
    body = {"topic": topic, "title": title, "message": message, "priority": priority}
    if click:
        body["click"] = click
    if actions:
        body["actions"] = actions
    req = urllib.request.Request("https://ntfy.sh", data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json", "User-Agent": C.USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            print("ntfy", r.status, title)
    except Exception as e:  # noqa: BLE001  通知失敗で監視全体を止めない
        print("ntfy error", e, file=sys.stderr)


# ---------------------------------------------------------------- fetchers
def fetch_pages(url_tpl: str, pages: range, shop: str, stop_below: int):
    """ページを順に取得。(items, ok_pages, ng_pages, 最後に取得したページ, 末尾到達) を返す。"""
    items, ok, ng, last, reached_end = [], 0, 0, None, False
    for page in pages:
        try:
            got = parse_list(http_get(url_tpl.format(page=page)), shop)
        except Exception as e:  # noqa: BLE001
            print(f"{shop} p{page} error: {e}", file=sys.stderr)
            ng += 1
            time.sleep(C.REQUEST_INTERVAL_SEC)
            break
        last = page
        if not got:
            ng += 1  # 200でも商品が1件も読めない＝ページ構造の変化を疑う
            reached_end = True
            break
        ok += 1
        items.extend(got)
        time.sleep(C.REQUEST_INTERVAL_SEC)
        if min(i.price for i in got) < stop_below:
            reached_end = True
            break
    return items, ok, ng, last, reached_end


def to_record(item, key, url: str, now: datetime) -> dict:
    return {"pid": item.pid, "name": item.raw_name, "price": item.price, "key": asdict(key),
            "url": url, "in_stock": in_stock(item) if item.shop == "cardrush" else True,
            "seen": now.isoformat()}


# ---------------------------------------------------------------- main
def run(now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    today = now.astimezone(JST).date().isoformat()
    state = load("state.json", {})
    first_run = not state
    a_rec = load("cardrush.json", {})
    stats = state.setdefault("daily", {}).setdefault(today, {"fetch_ok": 0, "fetch_ng": 0, "detections": 0, "notified": 0})

    # --- B：晴れる屋2ネット買取（毎回先頭から）
    min_b = C.MIN_A_PRICE + C.MIN_PROFIT + C.A_SHIPPING + C.B_SHIPPING + C.TRANSFER_FEE
    b_items, ok, ng, _, _ = fetch_pages(C.HARE2BUY_LIST_URL, range(1, C.HARE2BUY_MAX_PAGES + 1), "hare2buy", min_b)
    stats["fetch_ok"] += ok
    stats["fetch_ng"] += ng
    b_ok = ok > 0 and ng == 0
    alerts = []
    if update_failures(state, "hare2buy", b_ok):
        alerts.append("晴れる屋2ネット買取")
    b_rec = {}
    for it in b_items:
        k = hare2buy_key(it)
        if k:
            b_rec[it.pid] = to_record(it, k, C.HARE2BUY_PRODUCT_URL.format(pid=it.pid), now)
    if b_ok:
        save("hare2buy.json", b_rec)

    # --- A：カードラッシュ（前回の続きから数ページ）
    cursor = int(state.get("cardrush_cursor", 1))
    a_items, ok, ng, last, reached_end = fetch_pages(
        C.CARDRUSH_LIST_URL, range(cursor, cursor + C.CARDRUSH_PAGES_PER_RUN), "cardrush", C.MIN_A_PRICE)
    stats["fetch_ok"] += ok
    stats["fetch_ng"] += ng
    a_ok = ok > 0
    if update_failures(state, "cardrush", a_ok):
        alerts.append("カードラッシュ")
    for it in a_items:
        k = cardrush_key(it)
        if k:
            a_rec[it.pid] = to_record(it, k, C.CARDRUSH_PRODUCT_URL.format(pid=it.pid), now)
    if reached_end or not a_ok:
        state["cardrush_cursor"] = 1
    else:
        state["cardrush_cursor"] = (last or cursor) + 1
    a_rec = prune(a_rec, now)
    save("cardrush.json", a_rec)

    # --- 照合と通知（Bが今回取得できたときだけ。古い買取価格で通知しない）
    notified = {p: t for p, t in state.get("notified", {}).items()
                if now - datetime.fromisoformat(t) < timedelta(hours=C.RENOTIFY_HOURS)}
    if b_ok:
        opps = find_opportunities(a_rec, b_rec, now)
        stats["detections"] += len(opps)
        new_file = not os.path.exists(os.path.join(DATA_DIR, "detections.csv"))
        with open(os.path.join(DATA_DIR, "detections.csv"), "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if new_file:
                w.writerow(["detected_at", "card", "a_price", "b_price", "profit", "a_url", "b_url"])
            for o in opps:
                w.writerow([now.isoformat(), o["a"]["name"], o["a"]["price"], o["b"]["price"],
                            o["profit"], o["a"]["url"], o["b"]["url"]])
        for o in opps:
            if not should_notify(o["pair"], notified, now):
                continue
            a, b = o["a"], o["b"]
            notify(
                f"価格逆転 +{o['profit']:,}円",
                f"{a['name']}\nカードラッシュ販売 {a['price']:,}円 → 晴れる屋2買取 {b['price']:,}円\n"
                f"想定利益 {o['profit']:,}円（送料等差引後）\n"
                "※買取価格は美品の上限。傷があると減額で赤字の可能性あり。購入前に両方の最新価格を確認してください。",
                click=a["url"],
                actions=[{"action": "view", "label": "カードラッシュで見る", "url": a["url"]},
                         {"action": "view", "label": "晴れる屋2買取で見る", "url": b["url"]}],
                priority=4,
            )
            notified[o["pair"]] = now.isoformat()
            stats["notified"] += 1
    state["notified"] = notified

    # --- 死活監視
    for shop in alerts:
        notify("【エラー】取得失敗が続いています",
               f"{shop}の取得に{C.FAIL_ALERT_THRESHOLD}回連続で失敗しました。サイトの構造変更かアクセス制限の可能性があります。",
               priority=5)
    if first_run:
        notify("監視を開始しました",
               f"カードラッシュ→晴れる屋2ネット買取の価格逆転監視が動き始めました。\n今回の取得：買取{len(b_rec)}件、販売{len(a_items)}件。")
        state["last_heartbeat_date"] = now.astimezone(JST).date().isoformat()
    elif heartbeat_due(state, now):
        y = (now.astimezone(JST).date() - timedelta(days=1)).isoformat()
        ys = state["daily"].get(y, {"fetch_ok": 0, "fetch_ng": 0, "detections": 0, "notified": 0})
        notify("稼働中（毎朝の報告）",
               f"前日の取得成功 {ys['fetch_ok']}ページ／失敗 {ys['fetch_ng']}ページ\n"
               f"検知 {ys['detections']}件（うち通知 {ys['notified']}件）\n監視中の販売商品 {len(a_rec)}件")
        state["last_heartbeat_date"] = now.astimezone(JST).date().isoformat()
    # 日別統計は直近14日だけ残す
    state["daily"] = dict(sorted(state["daily"].items())[-14:])
    state["last_run"] = now.isoformat()
    save("state.json", state)
    print(json.dumps({"b_records": len(b_rec), "a_records": len(a_rec), "cursor": state["cardrush_cursor"],
                      "today": stats}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(run())
