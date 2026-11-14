"""一覧ページのHTMLから商品を取り出し、カードの同一判定用キーを作る。

カードラッシュ（販売）と晴れる屋2ネット買取は同じショップシステム（おちゃのこネット）で、
一覧のHTML構造は共通。商品名の書式だけが店ごとに違う。
  カードラッシュ : 〔状態B〕メガレックウザex【MUR】{113/076}   型番欄 = M6
  晴れる屋2買取  : メガレックウザex(MUR){竜}〈113/076〉[M6]
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass

_TAG = re.compile(r"<[^>]+>")


@dataclass
class Item:
    shop: str
    pid: str
    raw_name: str
    model: str          # 型番欄（カードラッシュでは収録弾コード）
    price: int
    stock_text: str


@dataclass(frozen=True)
class CardKey:
    set_code: str
    number: str
    rarity: str
    name: str           # 比較用に正規化した名前
    grade: str          # "RAW"（状態A・鑑定なし）または "PSA10"


def _text(s: str) -> str:
    return html.unescape(_TAG.sub("", s)).strip()


def parse_list(page_html: str, shop: str) -> list[Item]:
    items: list[Item] = []
    for block in page_html.split('<li class="list_item_cell')[1:]:
        pid = re.search(r"/product/(\d+)", block)
        name = re.search(r'<span class="goods_name">(.*?)</span>\s*(?:<wbr|<span class="model_number|</p>)', block, re.S)
        price = re.search(r'class="figure">(?:<span[^>]*>[^<]*</span>)?\s*([\d,]+)', block)
        if not (pid and name and price):
            continue
        model = re.search(r'model_number_value">([^<]*)<', block)
        stock = re.search(r'<p class="stock[^"]*">\s*(.*?)\s*</p>', block, re.S)
        items.append(Item(
            shop=shop,
            pid=pid.group(1),
            raw_name=_text(name.group(1)),
            model=_text(model.group(1)) if model else "",
            price=int(price.group(1).replace(",", "")),
            stock_text=_text(stock.group(1)) if stock else "",
        ))
    return items


# 名前に含まれていたら同一判定をあきらめる語（ミラー違い・エラー品などの取り違え防止）
_AMBIGUOUS = ("ミラー", "エラー", "※", "未開封", "パラレル", "海外", "英語", "中国", "韓国", "ジャンボ", "デッキ", "BOX", "セット")
_RARITY_ALIAS = {"P": "PROMO", "PR": "PROMO", "-": "D"}  # 「-」はデッキ収録などレアリティなし


def _norm_name(name: str) -> str:
    name = re.sub(r"\([^()]*仕様\)", "", name)     # 「(RR仕様)」などは表記ゆれとして除去
    name = name.replace(" ", "").replace("　", "")
    return name


def _norm_rarity(r: str) -> str:
    r = r.strip().upper()
    return _RARITY_ALIAS.get(r, r)


def cardrush_key(item: Item) -> CardKey | None:
    """カードラッシュの商品名からキーを作る。状態A（表記なし）とPSA10以外は対象外。"""
    name = item.raw_name
    grade = "RAW"
    m = re.match(r"^〔([^〕]*)〕", name)
    if m:
        tag = m.group(1)
        if tag in ("PSA10鑑定済",):
            grade = "PSA10"
        else:
            return None  # 状態B・C、PSA9など。買取価格（美品基準）と比べられない
        name = name[m.end():]
    if any(w in name for w in _AMBIGUOUS):
        return None
    m = re.match(r"^(?P<name>.+?)【(?P<rar>[^】]+)】\{(?P<num>[^}]+)\}$", name)
    if not m or not item.model or m.group("num") in ("-", "旧裏"):
        return None
    return CardKey(item.model.upper(), m.group("num"), _norm_rarity(m.group("rar")), _norm_name(m.group("name")), grade)


def hare2buy_key(item: Item) -> CardKey | None:
    """晴れる屋2ネット買取の商品名からキーを作る。"""
    name = item.raw_name
    grade = "RAW"
    m = re.match(r"^【([^】]*)】", name)
    if m:
        if m.group(1) == "PSA10":
            grade = "PSA10"
        else:
            return None
        name = name[m.end():]
    if any(w in name for w in _AMBIGUOUS):
        return None
    m = re.match(r"^(?P<name>.+)\((?P<rar>[^()]+)\)\{[^}]*\}〈(?P<num>[^〉]+)〉\[(?P<set>[^\]]+)\]$", name)
    if not m:
        return None
    return CardKey(m.group("set").upper(), m.group("num"), _norm_rarity(m.group("rar")), _norm_name(m.group("name")), grade)


def in_stock(item: Item) -> bool:
    m = re.search(r"在庫数\s*(\d+)", item.stock_text)
    return bool(m and int(m.group(1)) > 0)
