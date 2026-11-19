import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "agents"))
import images  # noqa: E402


def cand(title, text, lic="CC BY 4.0", url="https://x/1.jpg"):
    return {"source": "Wikimedia Commons", "url": url, "title": title, "creator": "A Person", "license": lic,
            "text": (title + " " + text).lower()}


class LicenseTests(unittest.TestCase):
    def test_reusable_licenses_pass(self):
        for name in ("CC0", "Public domain", "CC BY 4.0", "CC BY-SA 3.0", "PD-USGov-NASA", "CC BY 2.0"):
            self.assertTrue(images.license_ok(name), name)

    def test_restrictive_or_unclear_licenses_fail(self):
        for name in ("CC BY-NC 4.0", "CC BY-ND 2.0", "CC BY-NC-SA 3.0", "Fair use", "GFDL", "", "All rights reserved", "Unknown"):
            self.assertFalse(images.license_ok(name), name)


class RelevanceTests(unittest.TestCase):
    def test_subject_word_must_really_be_there(self):
        self.assertTrue(images.relevant(cand("Scotoplanes globosa", "a sea pig on the seafloor"), "sea pig"))
        self.assertFalse(images.relevant(cand("Earth Observations", "sea of pigment colours"), "sea pig"))  # pigment is not pig

    def test_a_mosasaur_is_not_a_prehistoric_whale(self):
        self.assertFalse(images.relevant(cand("Tylosaurus proriger", "a marine reptile"), "prehistoric whale"))

    def test_plural_forms_and_generic_words(self):
        self.assertTrue(images.relevant(cand("Whale skulls on display", "fossil"), "whale skull"))
        self.assertFalse(images.relevant(cand("Anything", "deep ocean science"), "deep ocean science"))  # only generic words

    def test_a_scientific_name_is_a_firm_match(self):
        self.assertTrue(images.relevant(cand("Scotoplanes globosa", "deep-sea holothurian"), "Scotoplanes sea pig"))
        self.assertFalse(images.relevant(cand("Scotoplanes globosa", "deep-sea holothurian"), "sea pig"))

    def test_a_different_compound_is_not_the_subject(self):
        self.assertFalse(images.relevant(cand("Ethylene-sulfide-3D-balls", "a molecule; it can be made from hydrogen"), "hydrogen sulfide"))
        self.assertTrue(images.relevant(cand("Hydrogen sulfide lake", "a lake"), "hydrogen sulfide"))

    def test_photos_beat_drawings_and_museum_shots(self):
        live = {"text": "scotoplanes in the sea", "w": 1500}
        museum = {"text": "scotoplanes globosa usnm specimen", "w": 4000}
        drawing = {"text": "scotoplanes engraving plate", "w": 3000}
        model = {"text": "model of scotoplanes", "w": 3000}
        order = sorted([drawing, museum, model, live], key=lambda c: -images.quality(c))
        self.assertIs(order[0], live)
        self.assertLess(images.quality(museum), images.quality(live))
        self.assertLess(images.quality(drawing), images.quality(museum))
        self.assertLess(images.quality(model), 0)

    def test_head_word_is_mandatory(self):
        self.assertFalse(images.relevant(cand("Owl skull", "asymmetry in hearing"), "whale skull asymmetry"))


class SearchTests(unittest.TestCase):
    def test_queries_get_shorter_but_never_a_lone_last_word(self):
        self.assertEqual(images.variants("sea pig deep sea"), ["sea pig deep sea", "sea pig deep", "sea pig", "deep sea"])
        self.assertEqual(images.variants("nebula"), ["nebula"])

    def test_find_many_returns_only_relevant_distinct_photos(self):
        page = {"query": {"pages": {
            "1": {"index": 1, "title": "File:Sea pig.jpg", "imageinfo": [{"url": "u1", "thumburl": "https://x/1.jpg", "width": 2000, "height": 1500, "mime": "image/jpeg",
                  "descriptionurl": "d", "extmetadata": {"LicenseShortName": {"value": "CC0"}, "Artist": {"value": "<b>Bob</b>"}, "ImageDescription": {"value": "A sea pig"}}}]},
            "2": {"index": 2, "title": "File:Truck.jpg", "imageinfo": [{"url": "u2", "thumburl": "https://x/2.jpg", "width": 2000, "height": 1500, "mime": "image/jpeg",
                  "descriptionurl": "d", "extmetadata": {"LicenseShortName": {"value": "CC0"}, "ImageDescription": {"value": "a truck"}}}]},
            "3": {"index": 3, "title": "File:Sea pig nc.jpg", "imageinfo": [{"url": "u3", "thumburl": "https://x/3.jpg", "width": 2000, "height": 1500, "mime": "image/jpeg",
                  "descriptionurl": "d", "extmetadata": {"LicenseShortName": {"value": "CC BY-NC 4.0"}, "ImageDescription": {"value": "a sea pig"}}}]},
            "4": {"index": 4, "title": "File:Sea pig small.jpg", "imageinfo": [{"url": "u4", "thumburl": "https://x/4.jpg", "width": 300, "height": 200, "mime": "image/jpeg",
                  "descriptionurl": "d", "extmetadata": {"LicenseShortName": {"value": "CC0"}, "ImageDescription": {"value": "a sea pig"}}}]}}}}
        got = images.find_many("sea pig", 3, fetch=lambda url: page)
        self.assertEqual([c["title"] for c in got], ["Sea pig.jpg"])  # truck: off-topic, nc: licence, small: resolution
        self.assertEqual(got[0]["creator"], "Bob")

    def test_network_failure_means_no_photo_not_a_crash(self):
        def boom(url):
            raise OSError("down")
        self.assertEqual(images.find_many("sea pig", 3, fetch=boom), [])

    def test_disabled_by_environment(self):
        import os
        os.environ["NO_IMAGES"] = "1"
        try:
            self.assertEqual(images.find_many("sea pig", 3, fetch=lambda u: 1 / 0), [])
        finally:
            del os.environ["NO_IMAGES"]

    def test_credit_line(self):
        self.assertEqual(images.credit(cand("t", "x", "CC0")), "A Person / CC0 (Wikimedia Commons)")


class RetryTests(unittest.TestCase):
    def test_throttling_is_retried_then_succeeds(self):
        import io
        import urllib.error
        from unittest import mock
        calls = []

        def fake(req, timeout=0):
            calls.append(1)
            if len(calls) < 3:
                raise urllib.error.HTTPError(req.full_url, 429, "Too Many Requests", {}, None)
            return io.BytesIO(b'{"ok": 1}')
        with mock.patch.object(images.urllib.request, "urlopen", fake), mock.patch.object(images.time, "sleep", lambda s: None):
            self.assertEqual(images.get_json("https://x/y"), {"ok": 1})
        self.assertEqual(len(calls), 3)

    def test_a_404_is_not_retried(self):
        import urllib.error
        from unittest import mock
        calls = []

        def fake(req, timeout=0):
            calls.append(1)
            raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)
        with mock.patch.object(images.urllib.request, "urlopen", fake), mock.patch.object(images.time, "sleep", lambda s: None):
            with self.assertRaises(urllib.error.HTTPError):
                images.get_json("https://x/y")
        self.assertEqual(len(calls), 1)

    def test_failures_are_recorded_not_silent(self):
        images.ERRORS.clear()

        def boom(url):
            raise OSError("down")
        self.assertEqual(images.find_many("sea pig", 2, fetch=boom), [])
        self.assertTrue(images.ERRORS and "OSError" in images.ERRORS[0])


class CaptionTests(unittest.TestCase):
    def test_credit_goes_before_the_hashtags_once(self):
        import render_slides as R
        cap = "Isi.\n\nSumber: X\n\n#a #b"
        out = R.caption_with_credits(cap, ["A / CC0 (Wikimedia Commons)"])
        self.assertEqual(out, "Isi.\n\nSumber: X\n\nFoto ilustrasi: A / CC0 (Wikimedia Commons).\n\n#a #b")
        self.assertEqual(R.caption_with_credits(out, ["A / CC0 (Wikimedia Commons)"]), out)

    def test_a_rerender_replaces_the_old_credit_line(self):
        import render_slides as R
        first = R.caption_with_credits("Teks\n\nSumber: X\n\n#a #b", ["A / CC0 (Wikimedia Commons)"])
        second = R.caption_with_credits(first, ["B / CC BY 2.0 (Wikimedia Commons)"])
        self.assertEqual(second.count("Foto ilustrasi"), 1)
        self.assertIn("B / CC BY 2.0", second)
        self.assertNotIn("A / CC0", second)
        self.assertTrue(second.endswith("#a #b"))

    def test_no_photos_no_change(self):
        import render_slides as R
        self.assertEqual(R.caption_with_credits("Isi", []), "Isi")


if __name__ == "__main__":
    unittest.main()
