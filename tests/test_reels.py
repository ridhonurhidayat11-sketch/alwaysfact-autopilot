"""Reels, illustration elements, infographics and the reel path of the referee, queue and publisher."""
import json
import shutil
import sys
import tempfile
import unittest
import wave
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

try:
    import PIL  # noqa: F401
except ImportError:  # the publish workflow runs the tests without Pillow
    raise unittest.SkipTest("Pillow not installed")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agents"))
sys.path.insert(0, str(ROOT / "publisher"))

import elements  # noqa: E402
import infographics  # noqa: E402
import reel  # noqa: E402
import reel_sheet  # noqa: E402
import writer  # noqa: E402
import autopilot  # noqa: E402
import publish  # noqa: E402

DEMO = json.loads((ROOT / "tests" / "fixtures" / "reel-demo.json").read_text(encoding="utf-8"))


def reel_post():
    p = json.loads(json.dumps(DEMO))
    p["caption"] = "Kenapa kentut bau? Sumber: contoh"
    p["hashtags"] = ["fakta", "sains", "tubuh", "alwaysfact", "kentut"]
    p["claims"] = [
        {"text": "Orang sehat kentut sekitar 14 sampai 20 kali setiap hari.", "source_name": "x", "source_url": "https://example.org/a"},
        {"text": "Antara 20% sampai 90% volume kentut adalah nitrogen.", "source_name": "x", "source_url": "https://example.org/b"},
        {"text": "Satu molekul hidrogen sulfida di antara 1 juta molekul gas lain sudah cukup menimbulkan bau.", "source_name": "x", "source_url": "https://example.org/c"},
    ]
    return p


class ReelValidationTests(unittest.TestCase):
    def test_demo_reel_is_valid(self):
        self.assertEqual(reel.validate_reel(DEMO), [])
        self.assertEqual(writer.validate_post(reel_post()), [])

    def test_too_few_beats_and_missing_voice_text_fail(self):
        p = reel_post()
        p["reel"]["beats"] = p["reel"]["beats"][:3]
        self.assertTrue(any("beats" in x for x in reel.validate_reel(p)))
        p = reel_post()
        p["reel"]["beats"][1]["say"] = ""
        self.assertTrue(any("say" in x for x in reel.validate_reel(p)))

    def test_a_viz_cannot_show_a_number_no_claim_contains(self):
        p = reel_post()
        p["reel"]["beats"][1]["viz"]["value"] = "99x"
        self.assertTrue(any("99" in x for x in writer.validate_post(p)))

    def test_carousel_slide_viz_and_takeaway_are_checked(self):
        slide = {"type": "fact", "title": "t", "body": "b", "visual": {"source": "wikimedia", "keywords": "k"},
                 "viz": {"kind": "stat", "value": "14-20x", "label": "kali"}, "takeaway": "x" * 120}
        post = {"slides": [{"type": "hook", "title": "h", "visual": {"source": "wikimedia"}}] + [dict(slide) for _ in range(5)]
                + [{"type": "cta", "title": "c", "visual": {"source": "wikimedia"}}],
                "hook_options": ["a", "b"], "caption": "Sumber: x", "hashtags": list("abcde"),
                "claims": [{"text": "14 sampai 20 kali", "source_url": "https://e.org/x"}, {"text": "b", "source_url": "https://e.org/y"}]}
        bad = writer.validate_post(post)
        self.assertTrue(any("takeaway" in x for x in bad))


class StorytellingTests(unittest.TestCase):
    def test_new_fields_are_validated(self):
        p = reel_post()
        p["reel"]["titles"] = ["only one title here"]
        self.assertTrue(any("titles" in x for x in reel.validate_reel(p)))
        p = reel_post()
        p["reel"]["beats"][1]["act"] = "boring"
        p["reel"]["beats"][2]["camera"] = "shake"
        p["reel"]["beats"][3]["claim"] = 99
        bad = " ".join(reel.validate_reel(p))
        self.assertIn("act", bad)
        self.assertIn("camera", bad)
        self.assertIn("claim", bad)

    def test_sixty_second_limit_is_enforced_through_the_word_count(self):
        p = reel_post()
        p["reel"]["beats"][1]["say"] = "kata " * 150
        self.assertTrue(any("words" in x for x in reel.validate_reel(p)))

    def test_timeline_infographic(self):
        spec = {"kind": "timeline", "items": [{"label": "6.3 juta tahun lalu", "text": "a"}, {"label": "5.6 juta tahun lalu", "text": "b"}]}
        self.assertTrue(infographics.valid(spec))
        self.assertFalse(infographics.valid({"kind": "timeline", "items": [{"text": "no label"}, {"label": "x"}]}))
        for pr in (0, 0.5, 1):
            self.assertEqual(infographics.render(spec, 500, 400, pr).size, (500, 400))

    def test_production_sheet_has_every_section(self):
        p = reel_post()
        p["reel"]["titles"] = ["Judul alternatif satu", "Judul alternatif dua", "Judul alternatif tiga"]
        p["reel"]["beats"][2]["say"] = "Menurut peneliti, ini kemungkinan benar."
        md = reel_sheet.sheet(p, [4.0] * len(p["reel"]["beats"]), ["A / CC0 (Wikimedia Commons)"])
        for head in ("## A.", "## B.", "## C.", "## D.", "## E.", "## F.", "## G.", "## Referensi"):
            self.assertIn(head, md)
        self.assertIn("Judul alternatif dua", md)
        self.assertIn("| 1 | 4.0 dtk |", md)
        self.assertIn("Menurut peneliti", md.split("Interpretasi")[1])


class ElementTests(unittest.TestCase):
    def test_every_icon_and_the_mascot_draw(self):
        for name in elements.ICONS:
            im = elements.sticker(name, 120)
            self.assertEqual(im.mode, "RGBA")
            self.assertGreater(im.getchannel("A").getbbox()[2], 50)
        for mood in ("wow", "happy", "think"):
            self.assertEqual(elements.mascot(100, mood).size, (100, 100))

    def test_icon_choice_follows_the_words(self):
        self.assertEqual(elements.pick_icon("Biang baunya: gas berbelerang"), "gas")
        self.assertEqual(elements.pick_icon("Babi laut"), "fish")
        self.assertEqual(elements.pick_icon("tidak ada kata kunci"), "bulb")


class InfographicTests(unittest.TestCase):
    SPECS = [{"kind": "stat", "value": "1-2x", "label": "x"},
             {"kind": "share", "items": [{"label": "a", "pct": 30}, {"label": "b", "pct": 70}]},
             {"kind": "compare", "items": [{"label": "a", "value": 3}, {"label": "b", "value": 5}], "unit": "cm"},
             {"kind": "dots", "total": 100, "highlight": 1, "label": "1 dari 100"},
             {"kind": "steps", "items": ["a", "b", "c"]}]

    def test_all_kinds_draw_at_any_progress(self):
        for spec in self.SPECS:
            self.assertTrue(infographics.valid(spec))
            for p in (0.0, 0.5, 1.0):
                self.assertEqual(infographics.render(spec, 600, 400, p).size, (600, 400))

    def test_broken_specs_are_refused(self):
        for bad in (None, {}, {"kind": "pie"}, {"kind": "stat", "value": "1"}, {"kind": "steps", "items": ["a"]},
                    {"kind": "share", "items": [{"label": "a"}, {"label": "b"}]}):
            self.assertFalse(infographics.valid(bad))
            self.assertIsNone(infographics.render(bad))


class QueueAndPublishTests(unittest.TestCase):
    def test_reels_take_reel_slots_and_carousels_the_rest(self):
        c = {"slots_wib": ["06:30", "12:00", "15:30", "19:30", "21:00"], "reel_slots_wib": ["12:00", "19:30"]}
        now = datetime(2026, 11, 20, 0, 0, tzinfo=timezone.utc)
        rows = []
        posts = [{"id": "r1", "format": "reel", "caption": "c", "hashtags": ["a"]},
                 {"id": "c1", "caption": "c", "hashtags": ["a"]}, {"id": "r2", "format": "reel", "caption": "c", "hashtags": ["a"]}]
        self.assertEqual(autopilot.enqueue(rows, posts, c, now), 3)
        when = {r["id"]: r["scheduled_at"] for r in rows}
        self.assertTrue(when["r1"].endswith("05:00:00Z"))   # 12:00 WIB
        self.assertTrue(when["r2"].endswith("12:30:00Z"))   # 19:30 WIB
        self.assertTrue(when["c1"].endswith("23:30:00Z") or when["c1"].endswith("08:30:00Z"))  # a carousel slot

    def test_reel_rows_are_published_as_reels(self):
        calls = []

        def fake_call(method, path, retries=2, **params):
            calls.append((method, path, params))
            return {"id": "cid", "status_code": "FINISHED"}
        row = {"media_urls": "https://cdn.jsdelivr.net/gh/u/r@main/media/012.mp4", "caption": "hai"}
        with mock.patch.object(publish, "call", fake_call), mock.patch.object(publish, "check_media"), \
                mock.patch.object(publish, "purge_cdn"):
            publish.publish(row, "123")
        create = next(p for m, path, p in calls if m == "POST" and path == "123/media")
        self.assertEqual(create["media_type"], "REELS")
        self.assertEqual(create["video_url"], row["media_urls"])
        self.assertTrue(any(path == "123/media_publish" for _, path, _ in calls))
        self.assertTrue(publish.is_reel([row["media_urls"]]))
        self.assertFalse(publish.is_reel(["https://x/a.jpg"]))


class PruneTests(unittest.TestCase):
    def test_only_old_posted_media_is_removed(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            for n in ("001-1.jpg", "001-2.jpg", "002.mp4", "003-1.jpg", "004-1.jpg"):
                (d / n).write_bytes(b"x")
            now = datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc)
            rows = [{"id": "001", "status": "posted", "posted_at": "2026-11-20T05:00:00Z"},
                    {"id": "002", "status": "posted", "posted_at": "2026-11-21T05:00:00Z"},
                    {"id": "003", "status": "posted", "posted_at": "2026-11-24T05:00:00Z"},
                    {"id": "004", "status": "approved", "posted_at": ""}]
            self.assertEqual(autopilot.prune_media(rows, now, d), 3)
            self.assertEqual(sorted(p.name for p in d.iterdir()), ["003-1.jpg", "004-1.jpg"])


def silent_tts(text, path):
    """Offline stand-in for the voice service: a silent wav (named .mp3, ffmpeg does not care) and even word timings."""
    words = text.split()
    secs = max(1.5, 0.35 * len(words))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        w.writeframes(b"\x00\x00" * int(24000 * secs))
    step = secs / max(1, len(words))
    return [(i * step, (i + 1) * step, wd) for i, wd in enumerate(words)]


class ReelRenderTests(unittest.TestCase):
    def test_a_reel_is_rendered_to_a_playable_h264_file(self):
        try:
            reel.ffmpeg_exe()
            import numpy  # noqa: F401
        except Exception:
            self.skipTest("ffmpeg or numpy not available here")
        post = json.loads(json.dumps(DEMO))
        post["reel"]["beats"] = post["reel"]["beats"][:5]
        post["reel"]["beats"][-1] = DEMO["reel"]["beats"][-1]
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "r.mp4"
            info = reel.render_reel(post, out, tts=silent_tts, fps=6)
            self.assertTrue(out.exists() and out.stat().st_size > 20_000)
            self.assertGreater(info["seconds"], 8)
            probe = __import__("subprocess").run([reel.ffmpeg_exe(), "-i", str(out)], capture_output=True, text=True).stderr
            self.assertIn("h264", probe)
            self.assertIn("1080x1920", probe)
            self.assertIn("aac", probe)

    def test_caption_chunks_break_at_punctuation(self):
        words = [(0, 1, "Satu"), (1, 2, "dua,"), (2, 3, "tiga"), (3, 4, "empat"), (4, 5, "lima"), (5, 6, "enam"), (6, 7, "tujuh.")]
        chunks = reel.chunk_words(words)
        self.assertEqual([len(c) for c in chunks], [2, 4, 1])


if __name__ == "__main__":
    unittest.main()
