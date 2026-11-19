"""Buffer posting backend: the GraphQL it writes, channel discovery, fallback, safe draft mode, backend choice."""
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "publisher"))
os.environ.setdefault("IG_TOKEN", "x")
import buffer_backend as B  # noqa: E402
import publish as P  # noqa: E402

CAROUSEL = {"id": "009", "caption": 'Kentut "bau" ?\nSumber: X\n\n#a #b', "media_urls": "https://x/a.jpg https://x/b.jpg https://x/c.jpg"}
REEL = {"id": "008", "caption": "Reel", "media_urls": "https://cdn.jsdelivr.net/gh/u/r@main/media/008.mp4"}


class MutationTests(unittest.TestCase):
    def test_carousel_is_several_images_of_type_post(self):
        q = B.build_mutation(CAROUSEL, "CH1")
        self.assertEqual(q.count("image: {"), 3)
        self.assertIn("type: post", q)
        self.assertIn("mode: shareNow", q)
        self.assertIn('channelId: "CH1"', q)
        self.assertIn('\\"bau\\"', q)           # quotes and newlines in the caption are escaped
        self.assertIn("\\n", q)
        self.assertNotIn("saveToDraft", q)

    def test_reel_is_one_video_of_type_reel(self):
        q = B.build_mutation(REEL, "CH1")
        self.assertIn("video: {", q)
        self.assertIn("thumbnailOffset: 1200", q)
        self.assertIn("type: reel", q)
        self.assertNotIn("image:", q)

    def test_draft_flag_and_schedule(self):
        self.assertIn("saveToDraft: true", B.build_mutation(CAROUSEL, "CH1", "addToQueue", draft=True))
        self.assertIn('dueAt: "2026-11-20T08:30:00.000Z"', B.build_mutation(CAROUSEL, "CH1", "customScheduled", due="2026-11-20T08:30:00.000Z"))


class FlowTests(unittest.TestCase):
    def setUp(self):
        B._CACHE.clear()
        os.environ.pop("BUFFER_CHANNEL_ID", None)

    def fake(self, mode_error=False):
        calls = []

        def gql(query, key=None, retries=2):
            calls.append(query)
            if "organizations" in query:
                return {"account": {"organizations": [{"id": "ORG1", "name": "o"}]}}
            if "channels(" in query:
                return {"channels": [{"id": "TT", "name": "t", "service": "tiktok"}, {"id": "IG1", "name": "alwaysfact_", "service": "instagram"}]}
            if "createPost" in query:
                if mode_error and "shareNow" in query:
                    return {"createPost": {"message": "Invalid value for enum mode"}}
                return {"createPost": {"post": {"id": "P1", "status": "scheduled", "dueAt": "x"}}}
            if "posts(" in query:
                return {"posts": {"edges": [{"node": {"id": "P0", "text": CAROUSEL["caption"]}}]}}
            raise AssertionError(query)
        return gql, calls

    def test_instagram_channel_is_found_by_service(self):
        gql, _ = self.fake()
        with mock.patch.object(B, "gql", gql):
            self.assertEqual(B.instagram_channel(), ("ORG1", "IG1"))

    def test_create_falls_back_to_a_scheduled_time_when_the_mode_is_refused(self):
        gql, calls = self.fake(mode_error=True)
        with mock.patch.object(B, "gql", gql):
            self.assertEqual(B.create(CAROUSEL), ("P1", "scheduled"))
        self.assertTrue(any("customScheduled" in c and "dueAt" in c for c in calls))

    def test_a_real_refusal_is_raised_not_retried_blindly(self):
        def gql(query, key=None, retries=2):
            if "organizations" in query:
                return {"account": {"organizations": [{"id": "O", "name": "o"}]}}
            if "channels(" in query:
                return {"channels": [{"id": "IG1", "service": "instagram"}]}
            return {"createPost": {"message": "Media URL is not reachable"}}
        with mock.patch.object(B, "gql", gql):
            with self.assertRaisesRegex(RuntimeError, "not reachable"):
                B.create(CAROUSEL)

    def test_duplicate_caption_is_detected(self):
        gql, _ = self.fake()
        with mock.patch.object(B, "gql", gql):
            self.assertTrue(B.already_live("", CAROUSEL["caption"]))
            self.assertFalse(B.already_live("", "something else entirely"))

    def test_a_draft_does_not_count_as_already_posted(self):
        def gql(query, key=None, retries=2):
            if "organizations" in query:
                return {"account": {"organizations": [{"id": "O", "name": "o"}]}}
            if "channels(" in query:
                return {"channels": [{"id": "IG1", "service": "instagram"}]}
            return {"posts": {"edges": [{"node": {"id": "p", "text": CAROUSEL["caption"], "status": "draft"}}]}}
        with mock.patch.object(B, "gql", gql):
            self.assertFalse(B.already_live("", CAROUSEL["caption"]))

    def test_draft_mode_creates_a_draft(self):
        gql, calls = self.fake()
        with mock.patch.object(B, "gql", gql), mock.patch.dict(os.environ, {"BUFFER_DRAFT": "1"}), \
                mock.patch.object(P, "check_media"), mock.patch.object(P, "purge_cdn"):
            out = B.publish(CAROUSEL)
        self.assertTrue(out.startswith("buffer:P1"))
        self.assertTrue(any("saveToDraft: true" in c for c in calls))


class BackendChoiceTests(unittest.TestCase):
    def test_buffer_is_used_only_when_the_key_is_set(self):
        with mock.patch.dict(os.environ, {"BUFFER_API_KEY": "k"}):
            self.assertIs(P.backend(), B)
        env = {k: v for k, v in os.environ.items() if k != "BUFFER_API_KEY"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertTrue(hasattr(P.backend(), "already_live") and P.backend() is not B)


if __name__ == "__main__":
    unittest.main()
