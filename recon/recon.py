"""ページ構造調査スクリプト。

urls.txt の各URLを1回ずつ取得し、HTML/テキストと取得結果の一覧を recon/out/ に保存する。
アクセス間隔は1リクエストごとに3秒空ける（サイトへの負荷を最小にするため）。
最後に ntfy へテスト通知を送り、通知経路も確認する。
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

UA = "toreca-arb/0.1 (personal price monitor; low frequency)"
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out")
MAX_SAVE = 400_000  # 1ファイルあたりの保存上限（バイト）


def fetch(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "ja"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            body = r.read()
            return r.status, dict(r.headers), body, r.geturl()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers or {}), e.read() if e.fp else b"", url
    except Exception as e:  # noqa: BLE001
        return None, {}, str(e).encode(), url


def notify(text: str) -> None:
    topic = os.environ.get("NTFY_TOPIC")
    if not topic:
        print("NTFY_TOPIC未設定のため通知スキップ")
        return
    req = urllib.request.Request(
        f"https://ntfy.sh/{topic}",
        data=text.encode("utf-8"),
        headers={"Title": "toreca-arb", "User-Agent": UA},
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        print("ntfy:", r.status)


def main() -> int:
    os.makedirs(OUT, exist_ok=True)
    summary = []
    with open(os.path.join(HERE, "urls.txt"), encoding="utf-8") as f:
        rows = [l.rstrip("\n").split("\t") for l in f if l.strip() and not l.startswith("#")]
    for shop, name, url in rows:
        status, headers, body, final = fetch(url)
        path = os.path.join(OUT, f"{shop}__{name}.html")
        with open(path, "wb") as w:
            w.write(body[:MAX_SAVE])
        summary.append({
            "shop": shop, "name": name, "url": url, "final_url": final,
            "status": status, "bytes": len(body),
            "content_type": headers.get("Content-Type"),
            "server": headers.get("Server"),
        })
        print(shop, name, status, len(body))
        time.sleep(3)
    with open(os.path.join(OUT, "summary.json"), "w", encoding="utf-8") as w:
        json.dump(summary, w, ensure_ascii=False, indent=2)
    ok = sum(1 for s in summary if s["status"] == 200)
    if os.environ.get("RECON_NOTIFY") != "1":
        return 0
    notify(f"テスト通知：ページ調査が完了しました（{ok}/{len(summary)}件取得成功）。この通知が届いていれば通知設定はOKです。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
