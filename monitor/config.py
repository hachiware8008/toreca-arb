"""設定値。金額はすべて円。"""

USER_AGENT = "toreca-arb/0.1 (personal price monitor; low frequency)"
REQUEST_INTERVAL_SEC = 1.5   # 1リクエストごとの待ち時間（サイト負荷を抑える）
REQUEST_TIMEOUT_SEC = 30

# --- 取得対象 -------------------------------------------------------------
# 仕入れ先A：カードラッシュ（販売価格・高い順の全件一覧をページ送りで巡回）
CARDRUSH_LIST_URL = "https://www.cardrush-pokemon.jp/product-list?keyword=&order=desc&num=100&page={page}"
CARDRUSH_PRODUCT_URL = "https://www.cardrush-pokemon.jp/product/{pid}"
CARDRUSH_PAGES_PER_RUN = 13   # 1回の実行で巡回するページ数（GitHub Actionsの無料枠に収めるため）

# 売り先B：晴れる屋2ネット買取（買取価格・高い順。毎回先頭から取得）
HARE2BUY_LIST_URL = "https://www.hare2buy.com/product-list?keyword=&order=desc&num=120&page={page}"
HARE2BUY_PRODUCT_URL = "https://www.hare2buy.com/product/{pid}"
HARE2BUY_MAX_PAGES = 6

# --- 通知条件 -------------------------------------------------------------
MIN_A_PRICE = 5000        # A販売価格の下限
MIN_PROFIT = 1000         # 利益の下限
MIN_PROFIT_RATE = 0.10    # 利益率の下限（利益 ÷ A販売価格）

# 経費（要確認：実際の送料・手数料に合わせて調整する。分からないうちは高めに見積もる）
A_SHIPPING = 500          # カードラッシュから届くまでの送料
B_SHIPPING = 500          # 晴れる屋2ネット買取へ送る送料
TRANSFER_FEE = 0          # 買取代金の振込手数料

RENOTIFY_HOURS = 24       # 同じ組み合わせを再通知しない時間
A_STALE_HOURS = 3         # この時間より古いA価格は比較に使わない
DROP_AFTER_HOURS = 24     # この時間見かけなかった商品は記録から外す

# --- 死活監視 -------------------------------------------------------------
HEARTBEAT_HOUR_JST = 9    # 毎日この時刻以降の最初の実行で稼働報告
FAIL_ALERT_THRESHOLD = 3  # 連続失敗回数がこれに達したら即時通知
