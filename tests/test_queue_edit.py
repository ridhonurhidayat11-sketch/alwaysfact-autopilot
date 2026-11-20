import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "agents"))
import queue_edit as Q  # noqa: E402


def rows():
    mk = lambda i, st, media="https://x/a.jpg": {"id": i, "scheduled_at": "2026-11-20T01:00:00Z", "media_urls": media, "caption": "c", "status": st, "result": "", "posted_at": ""}
    return [mk("001", "approved"), mk("002", "approved"), mk("003", "posted"), mk("004", "pending_media", ""), mk("005", "held")]


class ReslotTests(unittest.TestCase):
    CFG = {"slots_wib": ["07:30", "11:30", "13:30", "19:30", "21:30"]}

    def test_waiting_rows_get_the_next_slots_in_order_and_others_are_untouched(self):
        r = rows()                                  # 001, 002 approved, 003 posted, 004 pending_media, 005 held
        r[1]["scheduled_at"] = "2026-11-19T10:00:00Z"   # 002 is the oldest, so it goes first
        n = Q.reslot(r, self.CFG, "2026-11-20T18:30:00Z")   # 01:30 WIB on 21 Nov
        by = {x["id"]: x for x in r}
        self.assertEqual(n, 3)
        self.assertEqual(by["002"]["scheduled_at"], "2026-11-21T00:30:00Z")   # 07:30 WIB
        self.assertEqual(by["001"]["scheduled_at"], "2026-11-21T04:30:00Z")   # 11:30 WIB
        self.assertEqual(by["004"]["scheduled_at"], "2026-11-21T06:30:00Z")   # 13:30 WIB
        self.assertEqual(by["003"]["scheduled_at"], "2026-11-20T01:00:00Z")   # posted: never modified
        self.assertEqual(by["005"]["scheduled_at"], "2026-11-20T01:00:00Z")   # held: never modified

    def test_bad_start_time_raises(self):
        with self.assertRaises(ValueError):
            Q.reslot(rows(), self.CFG, "tomorrow")


class QueueEditTests(unittest.TestCase):
    def test_hold_and_release(self):
        r = rows()
        self.assertEqual(Q.hold(r, {"001", "003"}, "why"), 1)       # a posted row is never touched
        self.assertEqual([x["status"] for x in r][:3], ["held", "approved", "posted"])
        self.assertEqual(Q.release(r, {"001", "005", "004"}), 2)    # 004 has no media yet, but 005 and 001 do
        self.assertEqual(r[0]["status"], "approved")

    def test_reschedule_needs_a_valid_time(self):
        r = rows()
        self.assertEqual(Q.reschedule(r, "002", "2026-11-20T08:30:00Z"), 1)
        self.assertEqual(r[1]["scheduled_at"], "2026-11-20T08:30:00Z")
        with self.assertRaises(ValueError):
            Q.reschedule(r, "002", "tomorrow")
        self.assertEqual(Q.reschedule(r, "003", "2026-11-21T08:30:00Z"), 0)

    def test_only_formats_holds_the_other_format(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "001.json").write_text(json.dumps({"format": "reel"}))
            (Path(d) / "002.json").write_text(json.dumps({}))
            r = rows()
            self.assertEqual(Q.only_formats(r, "carousel", "reels on hold", d), 1)
            self.assertEqual(r[0]["status"], "held")
            self.assertEqual(r[1]["status"], "approved")


if __name__ == "__main__":
    unittest.main()


class RetryTests(unittest.TestCase):
    def test_retry_only_touches_rows_wrongly_marked_already_live(self):
        import queue_edit as Q
        rows = [
            {"id": "009", "status": "posted", "result": "already-live", "posted_at": "", "scheduled_at": "x", "media_urls": "u", "caption": "c"},
            {"id": "010", "status": "posted", "result": "buffer:abc:sending", "posted_at": "t", "scheduled_at": "x", "media_urls": "u", "caption": "c"},
        ]
        self.assertEqual(Q.retry(rows, {"009", "010"}), 1)
        self.assertEqual((rows[0]["status"], rows[0]["result"]), ("approved", ""))
        self.assertEqual(rows[1]["status"], "posted")
