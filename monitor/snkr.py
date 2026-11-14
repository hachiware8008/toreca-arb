"""スニダン PSA10 値下がり監視。GitHub Actions から2時間おきに実行される。

1. 1日1回、キーワードごとの検索結果（おすすめ順）上位からウォッチリストを作る
2. 毎回、ウォッチリストの商品ページから PSA10 の最安出品価格を記録する
3. PSA10 が上限以下で、過去7日の平均より一定以上安ければ ntfy に通知
4. 毎朝、カードごとの「買い目線」（メルカリの保存検索に使う上限価格）を送る
状態は DATA_DIR/snkr に保存（ワークフロー側で暗号化）。
"""
from __future__ import annotations

import html
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from . import config as C
from .core import JST
from .run import http_get, notify

SEARCH_URL = "https://snkrdunk.com/search?keywords={kw}"
PRODUCT_URL = "https://snkrdunk.com/apparels/{pid}"

KEYWORDS = ["リザードン", "ピカチュウ", "ゲンガー", "レックウザ"]
TOP_N_PER_KEYWORD = 20      # キーワードごとに検索結果の上位何件を見るか
MAX_PRICE = 50000           # PSA10 がこの金額以下のカードだけ対象
DROP_RATE = 0.15            # 平均からこの割合以上安くなったら通知
AVG_DAYS = 7                # 平均を取る期間
MIN_HISTORY_DAYS = 3        # 記録がこの日数たまるまで値下がり判定しない
KEEP_DAYS = 30
RENOTIFY_HOURS = 24
DIGEST_MAX = 12             # 朝の買い目線で送るカード数の上限
_NON_SINGLE = ("BOX", "ボックス", "デッキ", "セット", "スリーブ", "ファイル", "ケース", "コレクション")


def data_dir() -> str:
    return os.path.join(os.environ.get("DATA_DIR", "data"), "snkr")


def load(name, default):
    path = os.path.join(data_dir(), name)
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save(name, obj):
    os.makedirs(data_dir(), exist_ok=True)
    with open(os.path.join(data_dir(), name), "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)


# ---------------------------------------------------------------- parsing
def parse_search(page_html: str, keyword: str) -> list[dict]:
    """検索結果を表示順のまま返す。シングルカード（[弾 番号] 付き）で名前にキーワードを含むものだけ。"""
    out, seen = [], set()
    for pid, label in re.findall(r'href="https://snkrdunk.com/apparels/(\d+)" class="[^"]*productTile" aria-label="([^"]+)"', page_html):
        name = html.unescape(label).rsplit(" - ¥", 1)[0].strip()
        head = name.split("[", 1)[0]
        if pid in seen or "[" not in name or keyword not in head or any(w in head for w in _NON_SINGLE):
            continue
        seen.add(pid)
        out.append({"pid": pid, "name": name, "keyword": keyword})
    return out


def parse_psa10(page_html: str) -> int | None:
    """商品ページに埋め込まれた状態別最安値から PSA10 の最安出品価格を取り出す（出品なしは None）。"""
    t = page_html.replace('\\"', '"')
    m = re.search(r'"conditionCode":"trading_card_single_psa10"[^{}]*?"usedMinPrice":(\d+)[^{}]*?"hasListing":(true|false)', t)
    if not m or m.group(2) != "true":
        return None
    return int(m.group(1))


# ---------------------------------------------------------------- analysis
def average(history: list, now: datetime, days: int = AVG_DAYS) -> float | None:
    since = now - timedelta(days=days)
    vals = [p for ts, p in history if datetime.fromisoformat(ts) >= since]
    return sum(vals) / len(vals) if vals else None


def history_days(history: list, now: datetime) -> float:
    if not history:
        return 0.0
    return (now - datetime.fromisoformat(history[0][0])).total_seconds() / 86400


def drop_alert(history: list, price: int, now: datetime) -> float | None:
    """値下がり率を返す（通知条件を満たさなければ None）。今回の価格は history に入れる前に判定する。"""
    if price > MAX_PRICE or history_days(history, now) < MIN_HISTORY_DAYS:
        return None
    avg = average(history, now)
    if not avg:
        return None
    rate = 1 - price / avg
    return rate if rate >= DROP_RATE else None


# ---------------------------------------------------------------- main
def run(now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    jst = now.astimezone(JST)
    state = load("state.json", {})
    first_run = not state
    hist = load("history.json", {})
    watch = state.get("watchlist", [])

    # ウォッチリストは1日1回作り直す（おすすめ順の上位）
    if state.get("watchlist_date") != jst.date().isoformat() or not watch:
        new, ok = [], 0
        for kw in KEYWORDS:
            try:
                new += parse_search(http_get(SEARCH_URL.format(kw=quote(kw))), kw)[:TOP_N_PER_KEYWORD]
                ok += 1
            except Exception as e:  # noqa: BLE001
                print("search error", kw, e, file=sys.stderr)
            time.sleep(C.REQUEST_INTERVAL_SEC)
        if ok:
            seen, watch = set(), []
            for w in new:
                if w["pid"] not in seen:
                    seen.add(w["pid"])
                    watch.append(w)
            state["watchlist"] = watch
            state["watchlist_date"] = jst.date().isoformat()

    notified = {k: v for k, v in state.get("notified", {}).items()
                if now - datetime.fromisoformat(v) < timedelta(hours=RENOTIFY_HOURS)}
    latest = state.setdefault("latest", {})
    ok = ng = alerts = 0
    for w in watch:
        try:
            price = parse_psa10(http_get(PRODUCT_URL.format(pid=w["pid"])))
            ok += 1
        except Exception as e:  # noqa: BLE001
            print("product error", w["pid"], e, file=sys.stderr)
            ng += 1
            time.sleep(C.REQUEST_INTERVAL_SEC)
            continue
        time.sleep(C.REQUEST_INTERVAL_SEC)
        if price is None:
            continue
        h = hist.setdefault(w["pid"], [])
        rate = drop_alert(h, price, now)
        if rate is not None and w["pid"] not in notified:
            avg = average(h, now)
            notify(f"PSA10 値下がり -{rate:.0%}",
                   f"{w['name']}\nスニダンPSA10最安 {price:,}円（{AVG_DAYS}日平均 {avg:,.0f}円）\n"
                   "※平均は最安出品価格の記録から計算。購入前に出品の状態・写真を確認してください。",
                   click=PRODUCT_URL.format(pid=w["pid"]), priority=4)
            notified[w["pid"]] = now.isoformat()
            alerts += 1
        h.append([now.isoformat(), price])
        cutoff = now - timedelta(days=KEEP_DAYS)
        hist[w["pid"]] = [x for x in h if datetime.fromisoformat(x[0]) >= cutoff]
        latest[w["pid"]] = price
    state["notified"] = notified

    # 連続失敗の検知（全件失敗が3回続いたら通知）
    fails = state.get("consecutive_failures", 0)
    fails = fails + 1 if (watch and ok == 0) or not watch else 0
    if fails == C.FAIL_ALERT_THRESHOLD:
        notify("【エラー】スニダンの取得失敗が続いています",
               f"スニダンの取得に{C.FAIL_ALERT_THRESHOLD}回連続で失敗しました。アクセス制限かページ構造の変更の可能性があります。",
               priority=5)
    state["consecutive_failures"] = fails

    # 初回と毎朝：買い目線（メルカリの保存検索に使う上限価格）
    targets = [w for w in watch if latest.get(w["pid"]) and latest[w["pid"]] <= MAX_PRICE]
    if first_run:
        notify("スニダン監視を開始しました",
               f"{'・'.join(KEYWORDS)}のPSA10（{MAX_PRICE:,}円以下）を2時間おきに記録します。\n"
               f"対象 {len(targets)}枚。値下がり通知は記録が{MIN_HISTORY_DAYS}日たまってから始まります。")
        state["last_digest_date"] = jst.date().isoformat()
    elif jst.hour >= C.HEARTBEAT_HOUR_JST and state.get("last_digest_date") != jst.date().isoformat():
        lines = []
        for w in targets[:DIGEST_MAX]:
            avg = average(hist.get(w["pid"], []), now)
            line = f"・{w['name'].split('(')[0].strip()}：今 {latest[w['pid']]:,}円"
            if avg and history_days(hist[w["pid"]], now) >= MIN_HISTORY_DAYS:
                line += f"／買い目線 {avg * (1 - DROP_RATE):,.0f}円以下"
            lines.append(line)
        notify("スニダンPSA10 買い目線（毎朝）",
               (f"対象 {len(targets)}枚（上位{min(len(targets), DIGEST_MAX)}枚を表示）\n" + "\n".join(lines)) if lines
               else "対象カードの価格を取得できませんでした。")
        state["last_digest_date"] = jst.date().isoformat()

    state["last_run"] = now.isoformat()
    save("history.json", hist)
    save("state.json", state)
    print(json.dumps({"watch": len(watch), "fetched": ok, "errors": ng, "targets": len(targets), "alerts": alerts}))
    return 0


if __name__ == "__main__":
    sys.exit(run())
