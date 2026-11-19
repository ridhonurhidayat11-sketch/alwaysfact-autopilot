"""Fresh news: dates are read from RSS and Atom, old items are dropped, the newest come first."""
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "agents"))
import scout  # noqa: E402

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)
RSS = """<?xml version="1.0"?><rss><channel>
<item><title>Old story about whales</title><link>https://ex.org/old</link><description>x</description><pubDate>Mon, 02 Nov 2026 08:00:00 GMT</pubDate></item>
<item><title>Yesterday story about stars</title><link>https://ex.org/new</link><description>y</description><pubDate>Thu, 19 Nov 2026 09:30:00 +0000</pubDate></item>
<item><title>Undated story about rocks</title><link>https://ex.org/nodate</link><description>z</description></item>
</channel></rss>"""
ATOM = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
<entry><title>Atom today</title><link href="https://ex.org/atom"/><summary>s</summary><updated>2026-11-20T06:15:30.123Z</updated></entry></feed>"""


class DateTests(unittest.TestCase):
    def test_both_date_formats_are_read(self):
        self.assertEqual(scout.parse_date("Thu, 19 Nov 2026 09:30:00 +0700"), datetime(2026, 11, 19, 2, 30, tzinfo=timezone.utc))
        self.assertEqual(scout.parse_date("2026-11-20T06:15:30.123Z"), datetime(2026, 11, 20, 6, 15, 30, tzinfo=timezone.utc))
        self.assertIsNone(scout.parse_date("soon"))
        self.assertIsNone(scout.parse_date(""))

    def test_old_news_is_dropped_and_the_newest_comes_first(self):
        feeds = [{"name": "A", "url": "a"}, {"name": "B", "url": "b"}]
        cands, notes = scout.collect(feeds, 8, lambda u: RSS if u == "a" else ATOM, now=NOW, max_age_days=5)
        urls = [c["url"] for c in cands]
        self.assertNotIn("https://ex.org/old", urls)
        self.assertEqual(urls[0], "https://ex.org/atom")          # 2026-11-20 beats 2026-11-19
        self.assertEqual(urls[-1], "https://ex.org/nodate")       # undated items go last, not lost
        self.assertTrue(any("older than 5 days" in n for n in notes))
        self.assertTrue(cands[0]["published"].startswith("2026-11-20"))


if __name__ == "__main__":
    unittest.main()
