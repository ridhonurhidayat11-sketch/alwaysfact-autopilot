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
        self.assertTrue(images.relevant(cand("Fossil whale heads", "skulls"), "whale skull"))
        self.assertFalse(images.relevant(cand("Anything", "deep ocean science"), "deep ocean science"))  # only generic words

    def test_a_scientific_name_is_a_firm_match(self):
        self.assertTrue(images.relevant(cand("Scotoplanes globosa", "deep-sea holothurian"), "Scotoplanes sea pig"))
        self.assertFalse(images.relevant(cand("Scotoplanes globosa", "deep-sea holothurian"), "sea pig"))

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


class CaptionTests(unittest.TestCase):
    def test_credit_goes_before_the_hashtags_once(self):
        import render_slides as R
        cap = "Isi.\n\nSumber: X\n\n#a #b"
        out = R.caption_with_credits(cap, ["A / CC0 (Wikimedia Commons)"])
        self.assertEqual(out, "Isi.\n\nSumber: X\n\nFoto ilustrasi: A / CC0 (Wikimedia Commons).\n\n#a #b")
        self.assertEqual(R.caption_with_credits(out, ["A / CC0 (Wikimedia Commons)"]), out)

    def test_no_photos_no_change(self):
        import render_slides as R
        self.assertEqual(R.caption_with_credits("Isi", []), "Isi")


if __name__ == "__main__":
    unittest.main()
