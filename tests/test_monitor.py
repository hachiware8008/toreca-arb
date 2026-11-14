import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from monitor import config as C
from monitor import core, parsers, run as runmod

FIX = os.path.join(os.path.dirname(__file__), "fixtures")


def read(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as f:
        return f.read()


def item(shop, name, model="", price=10000, stock="在庫数 3枚", pid="1"):
    return parsers.Item(shop, pid, name, model, price, stock)


class ParserTest(unittest.TestCase):
    def test_cardrush_list(self):
        items = parsers.parse_list(read("cardrush_list.html"), "cardrush")
        self.assertGreaterEqual(len(items), 90)
        self.assertTrue(all(i.price > 0 and i.pid for i in items))

    def test_hare2buy_list(self):
        items = parsers.parse_list(read("hare2buy_list.html"), "hare2buy")
        self.assertGreaterEqual(len(items), 80)
        keyed = [parsers.hare2buy_key(i) for i in items]
        self.assertGreater(sum(k is not None for k in keyed), len(items) * 0.7)

    def test_cardrush_key(self):
        k = parsers.cardrush_key(item("cardrush", "メガレックウザex【MUR】{113/076}", "M6"))
        self.assertEqual((k.set_code, k.number, k.rarity, k.name, k.grade), ("M6", "113/076", "MUR", "メガレックウザex", "RAW"))
        k = parsers.cardrush_key(item("cardrush", "〔PSA10鑑定済〕ルギアV(SA)【SR】{110/098}", "S12"))
        self.assertEqual(k.grade, "PSA10")
        self.assertEqual(k.name, "ルギアV(SA)")
        self.assertIsNone(parsers.cardrush_key(item("cardrush", "〔状態B〕オリーヴァex【SR】{111/098}", "SV10")))
        self.assertIsNone(parsers.cardrush_key(item("cardrush", "ピカチュウ(マスターボールミラー)【C】{025/165}", "SV2a")))
        self.assertEqual(parsers.cardrush_key(item("cardrush", "リザードンex(RR仕様)【-】{009/049}", "SVG")).name, "リザードンex")

    def test_hare2buy_key(self):
        k = parsers.hare2buy_key(item("hare2buy", "メガレックウザex(MUR){竜}〈113/076〉[M6]"))
        self.assertEqual((k.set_code, k.number, k.rarity, k.name, k.grade), ("M6", "113/076", "MUR", "メガレックウザex", "RAW"))
        k = parsers.hare2buy_key(item("hare2buy", "ヒバニー(PROMO){炎}〈006/S-P〉[S-P]"))
        self.assertEqual(k.rarity, "PROMO")
        self.assertEqual(parsers.cardrush_key(item("cardrush", "ヒバニー【P】{006/S-P}", "S-P")), k)

    def test_in_stock(self):
        self.assertTrue(parsers.in_stock(item("cardrush", "x", stock="在庫数 2枚")))
        self.assertFalse(parsers.in_stock(item("cardrush", "x", stock="在庫数 0枚")))
        self.assertFalse(parsers.in_stock(item("cardrush", "x", stock="売り切れ")))


class CoreTest(unittest.TestCase):
    now = datetime(2026, 11, 15, 1, 0, tzinfo=timezone.utc)  # JST 10:00

    def test_profit_and_threshold(self):
        costs = C.A_SHIPPING + C.B_SHIPPING + C.TRANSFER_FEE
        self.assertEqual(core.profit(10000, 12000), 2000 - costs)
        self.assertTrue(core.is_opportunity(10000, 12000 + costs))    # 利益2000・20%
        self.assertFalse(core.is_opportunity(10000, 10900 + costs))   # 利益900 < 1000
        self.assertFalse(core.is_opportunity(20000, 21500 + costs))   # 利益1500だが7.5% < 10%
        self.assertFalse(core.is_opportunity(4000, 9000))             # A価格5000未満

    def _rec(self, shop, name, model, price, pid, seen=None):
        it = item(shop, name, model, price, pid=pid)
        key = parsers.cardrush_key(it) if shop == "cardrush" else parsers.hare2buy_key(it)
        return runmod.to_record(it, key, f"https://example/{shop}/{pid}", seen or self.now)

    def test_find_opportunities(self):
        a = {"1": self._rec("cardrush", "メガレックウザex【MUR】{113/076}", "M6", 10000, "1"),
             "2": self._rec("cardrush", "メガグソクムシャex【SAR】{107/076}", "M6", 10000, "2")}
        b = {"9": self._rec("hare2buy", "メガレックウザex(MUR){竜}〈113/076〉[M6]", "", 14000, "9"),
             "8": self._rec("hare2buy", "メガグソクムシャex(SAR){草}〈107/076〉[M6]", "", 10500, "8")}
        opps = core.find_opportunities(a, b, self.now)
        self.assertEqual([o["pair"] for o in opps], ["cardrush:1>hare2buy:9"])
        # 古いA価格は使わない
        a["1"]["seen"] = (self.now - timedelta(hours=C.A_STALE_HOURS + 1)).isoformat()
        self.assertEqual(core.find_opportunities(a, b, self.now), [])

    def test_renotify(self):
        n = {"p": (self.now - timedelta(hours=1)).isoformat()}
        self.assertFalse(core.should_notify("p", n, self.now))
        n["p"] = (self.now - timedelta(hours=C.RENOTIFY_HOURS)).isoformat()
        self.assertTrue(core.should_notify("p", n, self.now))

    def test_heartbeat_and_failures(self):
        self.assertTrue(core.heartbeat_due({}, self.now))
        self.assertFalse(core.heartbeat_due({"last_heartbeat_date": "2026-11-15"}, self.now))
        early = datetime(2026, 11, 14, 22, 0, tzinfo=timezone.utc)  # JST 7:00
        self.assertFalse(core.heartbeat_due({}, early))
        st = {}
        self.assertEqual([core.update_failures(st, "x", False) for _ in range(4)], [False, False, True, False])
        core.update_failures(st, "x", True)
        self.assertEqual(st["consecutive_failures"]["x"], 0)


class RunTest(unittest.TestCase):
    """通信を差し替えて1回分の処理を通しで実行する。"""

    def test_end_to_end(self):
        b_html = read("hare2buy_list.html")
        b_items = parsers.parse_list(b_html, "hare2buy")
        target = next(i for i in b_items if parsers.hare2buy_key(i) and i.price >= 7000)
        k = parsers.hare2buy_key(target)
        # Bの買取価格より十分安いAの商品を合成したページ
        a_price = int(target.price * 0.7)
        a_html = (f'<li class="list_item_cell"><a href="https://x/product/777"></a>'
                  f'<span class="goods_name">{k.name}【{k.rarity}】{{{k.number}}}</span><wbr />'
                  f'<span class="model_number_value">{k.set_code}</span>'
                  f'<span class="figure">{a_price:,}円</span><p class="stock">在庫数 2枚</p></li>')
        a_end = '<li class="list_item_cell"><a href="https://x/product/778"></a><span class="goods_name">安いカード【C】{001/100}</span><wbr /><span class="figure">100円</span></li>'

        def fake_get(url):
            if "hare2buy" in url:
                return b_html
            return a_html + a_end

        sent = []
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(runmod, "DATA_DIR", d), \
                mock.patch.object(runmod, "http_get", side_effect=fake_get), \
                mock.patch.object(runmod, "notify", side_effect=lambda t, m, **kw: sent.append(t)), \
                mock.patch.object(runmod.time, "sleep"):
            now = datetime(2026, 11, 15, 1, 0, tzinfo=timezone.utc)
            runmod.run(now)
            self.assertIn("監視を開始しました", sent)
            self.assertTrue(any(t.startswith("価格逆転") for t in sent), sent)
            self.assertTrue(os.path.exists(os.path.join(d, "detections.csv")))
            # 30分後の2回目：同じ組み合わせは再通知しない
            sent.clear()
            runmod.run(now + timedelta(minutes=30))
            self.assertFalse(any(t.startswith("価格逆転") for t in sent), sent)


if __name__ == "__main__":
    unittest.main()
