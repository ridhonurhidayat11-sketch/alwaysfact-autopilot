import copy
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "agents"))

import common  # noqa: E402
import factcheck  # noqa: E402
import pipeline  # noqa: E402
import scorer  # noqa: E402
import scout  # noqa: E402
import writer  # noqa: E402

RUBRIC = json.loads((HERE.parent / "config" / "rubric.json").read_text())


def resp(text, stop="end_turn", tin=100, tout=50):
    return NS(content=[NS(type="text", text=text)], stop_reason=stop, usage=NS(input_tokens=tin, output_tokens=tout))


class FakeClient:
    """Plays back scripted replies in order and records every request."""

    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []
        self.messages = NS(create=self._create)

    def _create(self, **kw):
        self.calls.append(kw)
        r = self.replies.pop(0)
        return r(kw) if callable(r) else r


def llm_for(client, budget=10.0, tmp=None):
    d = tmp or tempfile.mkdtemp()
    return common.Llm(client=client, budget_usd=budget, log_path=Path(d) / "usage.jsonl")


def good_post():
    slides = [{"type": "hook", "title": "Hook yang kuat", "body": "Venus berputar jauh lebih pelan daripada Bumi kita.", "visual": {"source": "nasa", "keywords": "venus"}}]
    for i in range(4):
        slides.append({"type": "fact", "title": f"Fakta {i}", "body": "Isi singkat.",
                       "visual": {"source": "code_chart", "keywords": "grafik"}})
    slides.append({"type": "cta", "title": "Ikuti", "body": "@alwaysfact_", "visual": {"source": "canva_stock", "keywords": "x"}})
    return {"topic": "Venus", "pillar": "Alam semesta dan fisika", "hook_options": ["Hook yang kuat", "Hook lain"],
            "slides": slides, "caption": "Venus itu aneh.\n\nSumber: NASA.", "hashtags": ["a", "b", "c", "d", "e"],
            "claims": [{"text": "Satu rotasi Venus 243 hari Bumi", "source_name": "NASA", "source_url": "https://nasa.gov/venus"},
                       {"text": "Satu tahun Venus 225 hari Bumi", "source_name": "NASA", "source_url": "https://nasa.gov/venus"}],
            "music_suggestions": ["tenang", "misterius", "megah"]}


def verdicts(ok=True):
    v = "supported" if ok else "unsupported"
    return [{"claim": 1, "verdict": v, "evidence_url": "https://science.nasa.gov/venus", "note": "ok"},
            {"claim": 2, "verdict": "supported", "evidence_url": "https://science.nasa.gov/venus", "note": "ok"},
            {"claim": 0, "verdict": "supported", "note": "ok"}]


RSS = """<?xml version="1.0"?><rss><channel>
<item><title>Bintang aneh</title><link>https://ex.org/a</link><description>&lt;p&gt;Ringkas &amp;amp; jelas&lt;/p&gt;</description></item>
<item><title>Tanpa link</title><description>x</description></item>
</channel></rss>"""
ATOM = """<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Atom satu</title>
<link href="https://ex.org/b"/><summary>Isi</summary></entry></feed>"""


class ContextTests(unittest.TestCase):
    """Slide text must be readable by a lay person from the first slide on."""

    def slides(self, **over):
        sl = [{"type": "hook", "title": "Hook", "body": "Voyager 1 adalah pesawat NASA yang diluncurkan tahun 1977."},
              {"type": "fact", "title": "Fakta", "body": "Sinyalnya butuh waktu lama untuk sampai ke Bumi."},
              {"type": "cta", "title": "Ikuti", "body": "@alwaysfact_"}]
        for k, v in over.items():
            idx, field = k.split("_")
            sl[int(idx)][field] = v
        return sl

    def test_clear_slides_pass(self):
        self.assertEqual(writer.context_problems(self.slides()), [])

    def test_first_slide_needs_a_plain_body(self):
        self.assertTrue(writer.context_problems(self.slides(**{"0_body": ""})))
        self.assertTrue(writer.context_problems(self.slides(**{"0_body": "Ya begitu."})))

    def test_hanging_this_in_the_first_slide_body(self):
        self.assertTrue(writer.context_problems(self.slides(**{"0_body": "Pesawat ini ternyata sangat jauh dari kita semua."})))

    def test_jargon_needs_an_explanation_in_the_same_sentence(self):
        bad = writer.context_problems(self.slides(**{"1_body": "Ini terjadi lewat evolusi konvergen pada hewan."}))
        self.assertTrue(any("konvergen" in b for b in bad))
        ok = writer.context_problems(self.slides(**{"1_body": "Ini disebut evolusi konvergen, yaitu makhluk berbeda yang bentuknya jadi mirip."}))
        self.assertEqual(ok, [])


class FeedTests(unittest.TestCase):
    def test_parse_rss_strips_html_and_skips_items_without_link(self):
        items = scout.parse_feed(RSS, "Ex")
        self.assertEqual(len(items), 1)
        self.assertEqual((items[0]["title"], items[0]["url"], items[0]["summary"]), ("Bintang aneh", "https://ex.org/a", "Ringkas & jelas"))

    def test_parse_atom(self):
        self.assertEqual(scout.parse_feed(ATOM, "Ex")[0]["url"], "https://ex.org/b")

    def test_entity_bomb_is_rejected(self):
        bomb = '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><rss><channel></channel></rss>'
        with self.assertRaises(ValueError):
            scout.parse_feed(bomb, "Ex")

    def test_dead_feed_does_not_stop_the_others(self):
        def fetcher(url):
            if "dead" in url:
                raise TimeoutError("timed out")
            return RSS
        feeds = [{"name": "A", "url": "https://dead"}, {"name": "B", "url": "https://ok"}]
        cands, errors = scout.collect(feeds, 8, fetcher)
        self.assertEqual(len(cands), 1)
        self.assertEqual(len(errors), 1)
        self.assertTrue(cands[0]["id"].startswith("t"))

    def test_same_url_from_two_feeds_counts_once(self):
        cands, _ = scout.collect([{"name": "A", "url": "u1"}, {"name": "B", "url": "u2"}], 8, lambda u: RSS)
        self.assertEqual(len(cands), 1)


class CoreTests(unittest.TestCase):
    def test_extract_json_handles_fences_and_chatter(self):
        self.assertEqual(common.extract_json('Berikut:\n```json\n{"a": 1}\n```'), {"a": 1})
        self.assertEqual(common.extract_json("ok [1, 2] selesai"), [1, 2])
        with self.assertRaises(ValueError):
            common.extract_json("tidak ada json")

    def test_budget_stops_the_second_call(self):
        c = FakeClient(resp("{}", tin=2_000_000, tout=0), resp("{}"))
        llm = llm_for(c, budget=0.05)
        llm.call("t", common.SONNET, "s", "u")
        with self.assertRaises(common.BudgetExceeded):
            llm.call("t", common.SONNET, "s", "u")
        self.assertEqual(len(c.calls), 1)

    def test_usage_is_logged_with_cost(self):
        d = tempfile.mkdtemp()
        llm = llm_for(FakeClient(resp("{}", tin=1_000_000, tout=1_000_000)), tmp=d)
        llm.call("scorer", common.HAIKU, "s", "u")
        row = json.loads((Path(d) / "usage.jsonl").read_text().splitlines()[0])
        self.assertEqual((row["agent"], row["in"], row["out"], row["usd"]), ("scorer", 1_000_000, 1_000_000, 0.6))

    def test_pause_turn_continues_and_adds_tokens(self):
        c = FakeClient(resp("bagian satu ", stop="pause_turn", tin=10, tout=5), resp("bagian dua", tin=20, tout=7))
        llm = llm_for(c)
        self.assertEqual(llm.call("t", common.SONNET, "s", "u"), "bagian satu bagian dua")
        self.assertEqual(len(c.calls), 2)
        self.assertEqual(c.calls[1]["messages"][-1]["role"], "assistant")

    def test_refusal_and_truncation_are_errors(self):
        with self.assertRaises(common.LlmRefused):
            llm_for(FakeClient(resp("", stop="refusal"))).call("t", common.SONNET, "s", "u")
        with self.assertRaises(common.LlmTruncated):
            llm_for(FakeClient(resp("{", stop="max_tokens"))).call("t", common.SONNET, "s", "u")

    def test_duplicate_detection(self):
        self.assertTrue(common.is_duplicate("Gurita punya tiga jantung", ["Gurita punya 3 jantung dan darah biru"], 0.6))
        self.assertFalse(common.is_duplicate("Venus berputar mundur", ["Gurita punya tiga jantung"]))


class ScorerTests(unittest.TestCase):
    def cands(self):
        return [{"id": "t1", "origin": "feed", "title": "Gurita punya tiga jantung", "summary": "s", "url": "https://a", "source": "A"},
                {"id": "t2", "origin": "feed", "title": "Venus berputar mundur", "summary": "s", "url": "https://b", "source": "B"},
                {"id": "t3", "origin": "feed", "title": "Gurita memiliki 3 jantung", "summary": "s", "url": "https://c", "source": "C"},
                {"id": "t4", "origin": "feed", "title": "Rating rusak", "summary": "s", "url": "https://d", "source": "D"}]

    def reply(self):
        def sc(v): return {k["key"]: v for k in RUBRIC["criteria"]}
        return json.dumps([
            {"id": "t1", "scores": sc(5), "pillar": "Hewan dan alam", "angle": "a", "reason": "r"},
            {"id": "t2", "scores": sc(3), "pillar": "Alam semesta dan fisika", "angle": "a", "reason": "r"},
            {"id": "t3", "scores": sc(4), "pillar": "Hewan dan alam", "angle": "a", "reason": "r"},
            {"id": "t4", "scores": {"surprise": 9}, "pillar": "x", "angle": "a", "reason": "r"}])

    def test_weighted_math_and_validation(self):
        self.assertEqual(scorer.weighted({k["key"]: 5 for k in RUBRIC["criteria"]}, RUBRIC), 100.0)
        self.assertEqual(scorer.weighted({k["key"]: 1 for k in RUBRIC["criteria"]}, RUBRIC), 20.0)
        with self.assertRaises(ValueError):
            scorer.weighted({"surprise": 6}, RUBRIC)

    def test_ranking_dedupe_and_bad_ratings(self):
        llm = llm_for(FakeClient(resp(self.reply())))
        topics, dropped = scorer.score(llm, self.cands(), RUBRIC, history=[], top_n=10)
        self.assertEqual([t["id"] for t in topics], ["t1", "t2"])  # t3 repeats t1, t4 has bad ratings
        self.assertEqual(len(dropped), 1)
        self.assertEqual(topics[0]["score"], 100.0)

    def test_history_repeat_is_dropped(self):
        llm = llm_for(FakeClient(resp(self.reply())))
        topics, _ = scorer.score(llm, self.cands(), RUBRIC, history=["Venus berputar mundur"], top_n=10)
        self.assertNotIn("t2", [t["id"] for t in topics])

    def test_run_keeps_your_gate1_choices(self):
        d = Path(tempfile.mkdtemp())
        (d / "state").mkdir()
        chosen = {"id": "told", "title": "Topik lama yang Anda pilih", "decision": "select", "status": "new", "score": 80}
        common.save_json(d / "state" / "topics.json", [chosen])
        common.save_json(d / "state" / "candidates.json", self.cands())
        with mock.patch.object(scorer, "STATE", d / "state"), mock.patch.object(common, "STATE", d / "state"), \
                mock.patch.object(common, "CONTENT", d / "content"):
            merged, _ = scorer.run(llm_for(FakeClient(resp(self.reply()))))
        self.assertIn("told", [t["id"] for t in merged])
        self.assertTrue((d / "state" / "topics.csv").exists())


class WriterTests(unittest.TestCase):
    def test_good_post_has_no_problems(self):
        self.assertEqual(writer.validate_post(good_post()), [])

    def test_each_rule_is_enforced(self):
        def broken(fn):
            p = copy.deepcopy(good_post())
            fn(p)
            return writer.validate_post(p)
        self.assertTrue(broken(lambda p: p["slides"].pop()))
        self.assertTrue(broken(lambda p: p["slides"][1].update(body="x" * 300)))
        self.assertTrue(broken(lambda p: p["slides"][0].update(type="fact")))
        self.assertTrue(broken(lambda p: p["slides"][2]["visual"].update(source="google_images")))
        self.assertTrue(broken(lambda p: p["claims"][0].update(source_url="")))
        self.assertTrue(broken(lambda p: p["claims"][0].update(source_url="http://insecure.example")))
        self.assertTrue(broken(lambda p: p.update(caption="tanpa penutup")))
        self.assertTrue(broken(lambda p: p.update(claims=p["claims"][:1])))
        self.assertTrue(broken(lambda p: p.update(hashtags=["a"])))


class FactcheckTests(unittest.TestCase):
    def test_passes_only_when_every_claim_is_supported_with_evidence(self):
        self.assertTrue(factcheck.passes(good_post(), verdicts(True))[0])
        ok, problems = factcheck.passes(good_post(), verdicts(False))
        self.assertFalse(ok)
        self.assertIn("claim 1 unsupported", problems[0])

    def test_missing_overall_or_evidence_fails(self):
        v = verdicts(True)
        self.assertFalse(factcheck.passes(good_post(), v[:2])[0])
        v[0]["evidence_url"] = ""
        self.assertFalse(factcheck.passes(good_post(), v)[0])

    def test_unchecked_claim_fails(self):
        self.assertFalse(factcheck.passes(good_post(), [verdicts(True)[0], verdicts(True)[2]])[0])

    def test_check_post_sends_web_search_tool(self):
        c = FakeClient(resp(json.dumps(verdicts(True))))
        ok, _, _ = factcheck.check_post(llm_for(c), good_post())
        self.assertTrue(ok)
        self.assertEqual(c.calls[0]["tools"][0]["name"], "web_search")


class PipelineTests(unittest.TestCase):
    TOPIC = {"id": "t1", "title": "Venus", "summary": "s", "url": "https://nasa.gov", "pillar": "p", "angle": "a"}

    def test_failed_fact_check_triggers_guided_rewrite_then_passes(self):
        c = FakeClient(resp(json.dumps(good_post())), resp(json.dumps(verdicts(False))),
                       resp(json.dumps(good_post())), resp(json.dumps(verdicts(True))))
        post, report = pipeline.make_post(llm_for(c), self.TOPIC)
        self.assertIsNotNone(post)
        self.assertEqual(len(c.calls), 4)
        self.assertIn("claim 1 unsupported", c.calls[2]["messages"][0]["content"])  # rewrite got the feedback
        self.assertEqual([r["stage"] for r in report], ["factcheck", "factcheck"])

    def test_gives_up_after_two_rewrites(self):
        script = []
        for _ in range(3):
            script += [resp(json.dumps(good_post())), resp(json.dumps(verdicts(False)))]
        post, report = pipeline.make_post(llm_for(FakeClient(*script)), self.TOPIC)
        self.assertIsNone(post)
        self.assertEqual(len(report), 3)

    def test_structure_problems_skip_the_expensive_fact_check(self):
        bad = good_post()
        bad["slides"] = bad["slides"][:2]
        c = FakeClient(resp(json.dumps(bad)), resp(json.dumps(good_post())), resp(json.dumps(verdicts(True))))
        post, report = pipeline.make_post(llm_for(c), self.TOPIC)
        self.assertIsNotNone(post)
        self.assertEqual(report[0]["stage"], "structure")
        self.assertEqual(len(c.calls), 3)

    def test_write_command_only_touches_selected_topics_and_saves_post(self):
        d = Path(tempfile.mkdtemp())
        (d / "state").mkdir()
        (d / "content").mkdir()
        topics = [dict(self.TOPIC, decision="select", status="new"),
                  dict(self.TOPIC, id="t2", title="Lain", decision="", status="new")]
        common.save_json(d / "state" / "topics.json", topics)
        c = FakeClient(resp(json.dumps(good_post())), resp(json.dumps(verdicts(True))))
        with mock.patch.object(pipeline, "STATE", d / "state"), mock.patch.object(pipeline, "CONTENT", d / "content"), \
                mock.patch.object(common, "STATE", d / "state"), mock.patch.object(common, "CONTENT", d / "content"):
            made = pipeline.cmd_write(llm_for(c))
        self.assertEqual(made, ["001"])
        saved = json.loads((d / "content" / "001.json").read_text())
        self.assertEqual((saved["status"], saved["topic_id"]), ("ready_for_design", "t1"))
        after = json.loads((d / "state" / "topics.json").read_text())
        self.assertEqual([t["status"] for t in after], ["ready", "new"])
        self.assertEqual(len(c.calls), 2)


if __name__ == "__main__":
    unittest.main()


class HookForLayReadersTests(unittest.TestCase):
    def test_everyday_hooks_pass(self):
        import writer
        for h in ("Bagian kentut yang bikin bau cuma sebutir jagung di satu truk",
                  "Kenapa kita selalu menguap kalau melihat orang lain menguap?",
                  "Kepala kuda raksasa ada di langit, bukan lukisan"):
            self.assertEqual(writer.hook_problems(h), [], h)

    def test_jargon_is_rejected(self):
        import writer
        self.assertTrue(writer.hook_problems("Natrium di udara mengubah warna api hidrogen"))
        self.assertTrue(writer.hook_problems("Molekul aneh ini ditemukan di awan"))

    def test_too_many_foreign_names_are_rejected(self):
        import writer
        self.assertTrue(writer.hook_problems("Kata Adelaide dan Stonehenge dan Salisbury tentang batu"))

    def test_a_hanging_ini_is_rejected(self):
        import writer
        self.assertTrue(writer.hook_problems("Hewan laut dalam ini menyimpan rahasia"))
        self.assertEqual(writer.hook_problems("Ikan lele menyimpan rahasia"), [])
