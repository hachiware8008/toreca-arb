import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from monitor import snkr

FIX = os.path.join(os.path.dirname(__file__), "fixtures")
NOW = datetime(2026, 11, 20, 1, 0, tzinfo=timezone.utc)  # JST 10:00


def read(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as f:
        return f.read()


class ParseTest(unittest.TestCase):
    def test_search(self):
        items = snkr.parse_search(read("snkr_search.html"), "リザードン")
        self.assertGreaterEqual(len(items), 8)
        self.assertTrue(all("[" in i["name"] and "リザードン" in i["name"].split("[")[0] for i in items))
        names = " ".join(i["name"] for i in items)
        self.assertNotIn("デッキセット", names)   # シングル以外は除外

    def test_psa10(self):
        self.assertEqual(snkr.parse_psa10(read("snkr_product.html")), 26900)
        escaped = read("snkr_product.html").replace('"', '\\"')
        self.assertEqual(snkr.parse_psa10(escaped), 26900)
        self.assertIsNone(snkr.parse_psa10(read("snkr_product.html").replace(
            '"usedMinPrice":26900,"text":"PSA10","hasListing":true', '"usedMinPrice":26900,"text":"PSA10","hasListing":false')))
        self.assertIsNone(snkr.parse_psa10("<html></html>"))


class AlertTest(unittest.TestCase):
    def hist(self, days, price):
        return [[(NOW - timedelta(days=d)).isoformat(), price] for d in range(days, 0, -1)]

    def test_drop(self):
        h = self.hist(5, 20000)
        self.assertAlmostEqual(snkr.drop_alert(h, 16000, NOW), 0.20)
        self.assertIsNone(snkr.drop_alert(h, 18000, NOW))            # -10% は通知しない
        self.assertIsNone(snkr.drop_alert(self.hist(2, 20000), 10000, NOW))  # 記録2日では判定しない
        self.assertIsNone(snkr.drop_alert(self.hist(5, 80000), 60000, NOW))  # 上限5万円超は対象外


class RunTest(unittest.TestCase):
    def test_end_to_end(self):
        search = read("snkr_search.html")
        product = read("snkr_product.html")
        cheap = product.replace('"usedMinPrice":26900', '"usedMinPrice":20000')

        sent = []
        pages = {"p": product}

        def fake_get(url):
            return search if "search" in url else pages["p"]

        with tempfile.TemporaryDirectory() as d, \
                mock.patch.dict(os.environ, {"DATA_DIR": d}), \
                mock.patch.object(snkr, "http_get", side_effect=fake_get), \
                mock.patch.object(snkr, "notify", side_effect=lambda t, m, **kw: sent.append((t, m))), \
                mock.patch.object(snkr.time, "sleep"):
            start = NOW - timedelta(days=4)
            snkr.run(start)
            self.assertEqual(sent[0][0], "スニダン監視を開始しました")
            # 4日間、2時間おきに 26,900円 を記録
            t = start
            while t < NOW - timedelta(hours=2):
                t += timedelta(hours=2)
                snkr.run(t)
            sent.clear()
            pages["p"] = cheap            # 26,900 → 20,000（約-26%）
            snkr.run(NOW)
            drops = [s for s in sent if s[0].startswith("PSA10 値下がり")]
            self.assertTrue(drops, sent)
            # 2時間後：同じカードは24時間以内に再通知しない
            sent.clear()
            snkr.run(NOW + timedelta(hours=2))
            self.assertFalse([s for s in sent if s[0].startswith("PSA10 値下がり")])


if __name__ == "__main__":
    unittest.main()
