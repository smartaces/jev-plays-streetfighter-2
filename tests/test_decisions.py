import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx2
from typesafe_sdk import AsyncTypeSafeClient, RetryPolicy

from controller.config import Settings
from controller.jev import diagnostic_questions, request_choice
from controller.report import latest_completed_play, read_decisions, write_report
from test_controller import response_body


def diagnostic_answer(name, choice):
    return {"type": "choice", "choice": choice, "confidence": 0.6,
            "probabilities": {key: float(key == choice) for key in diagnostic_questions()[name]["criteria"]}}


class DiagnosticTests(unittest.IsolatedAsyncioTestCase):
    async def ask(self, body):
        seen = []
        def handler(request):
            seen.append(json.loads(request.content))
            return httpx2.Response(200, json=body)
        async with AsyncTypeSafeClient(api_key="offline-test-key", transport=httpx2.MockTransport(handler),
                                       retry=RetryPolicy(max_retries=0)) as client:
            result = await request_choice(client, {"distance": 20}, Settings())
        self.assertEqual(len(seen), 1)
        self.assertEqual(set(seen[0]["questions"]), {"next_action", "attack_strength", "main_threat", "opening"})
        return result

    async def test_diagnostics_in_one_call_do_not_override_action(self):
        body = response_body("sweep", "heavy")
        # Deliberate disagreement: diagnostics are information, not policy gates.
        body["answers"]["main_threat"] = diagnostic_answer("main_threat", "incoming_projectile")
        body["answers"]["opening"] = diagnostic_answer("opening", "defend_or_wait")
        body["answers"]["next_action"]["confidence"] = 0.02
        result = await self.ask(body)
        self.assertEqual((result["action"], result["strength"], result["confidence"]), ("sweep", "heavy", 0.02))
        self.assertEqual(result["diagnostics"]["opening"]["choice"], "defend_or_wait")
        self.assertEqual(result["diagnostic_errors"], {})

    async def test_missing_diagnostics_preserve_action(self):
        result = await self.ask(response_body("hurricane_kick"))
        self.assertEqual(result["action"], "hurricane_kick")
        self.assertEqual(result["diagnostics"], {})
        self.assertEqual(set(result["diagnostic_errors"].values()), {"not_returned"})

    async def test_invalid_diagnostic_probabilities_preserve_action(self):
        body = response_body("jump_back_kick")
        answer = diagnostic_answer("main_threat", "none_visible")
        answer["probabilities"]["none_visible"] = 0.2
        body["answers"]["main_threat"] = answer
        result = await self.ask(body)
        self.assertEqual(result["action"], "jump_back_kick")
        self.assertEqual(result["diagnostic_errors"]["main_threat"], "invalid_answer")


class ReportTests(unittest.TestCase):
    def run_files(self, path, mode="play", complete=True):
        path.mkdir(parents=True, exist_ok=True)
        (path / "manifest.json").write_text(json.dumps({"run_id": path.name, "mode": mode, "complete": complete,
             "question_version": "fixture", "questions": {"next_action": "<script>bad()</script>"}}))
        state = {"distance": 20, "opponent": {"character": "<script>bad()</script>"}}
        snapshot = {"frame": 5, "state": state}
        events = [{"event": "request", "job": {"request_id": 1, "snapshot": snapshot}},
                  {"event": "result", "result": {"request_id": 1, "snapshot": snapshot, "action": "sweep", "strength": "heavy", "started": 1, "received": 1.3}},
                  {"event": "applied", "request_id": 1, "action": "sweep", "strength": "heavy", "age_ms": 310},
                  {"event": "request", "job": {"request_id": 2, "snapshot": snapshot}},
                  {"event": "discarded", "request_id": 2, "reason": "stale"}]
        (path / "events.jsonl").write_text("\n".join(json.dumps(e) for e in events) + '\n{"unfinished":')

    def test_report_joins_application_and_discard_preserves_state_escapes_html(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            self.run_files(path)
            _, rows, skipped = read_decisions(path)
            self.assertEqual([row["disposition"] for row in rows], ["applied", "discarded: stale"])
            self.assertEqual(skipped, 1)
            page = write_report(path).read_text()
            self.assertNotIn("<script>bad()", page)
            self.assertIn("&lt;script&gt;bad()", page)
            self.assertIn("Unavailable (older run, mock, or missing answer)", page)
            self.assertIn("300 ms", page)
            self.assertIn("sweep 1", page)

    def test_latest_report_ignores_newer_mock_and_unfinished_runs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.run_files(root / "01-play")
            self.run_files(root / "02-mock", mode="mock")
            self.run_files(root / "03-live", complete=False)
            self.assertEqual(latest_completed_play(root), root / "01-play")

    def test_recorder_report_failure_does_not_break_logs(self):
        from controller.recording import Recorder
        with tempfile.TemporaryDirectory() as directory:
            recorder = Recorder.__new__(Recorder)
            from queue import Queue
            from threading import Event
            recorder.path = Path(directory)
            recorder.queue, recorder.stopping = Queue(), Event()
            recorder.stopping.set()
            recorder.summary, recorder.manifest = {}, {}
            recorder.error, recorder.lost_events = None, 0
            with patch("controller.report.write_report", side_effect=OSError("fixture")):
                recorder._write_loop()
            self.assertIsNone(recorder.error)
            self.assertTrue(json.loads((recorder.path / "manifest.json").read_text())["complete"])


if __name__ == "__main__":
    unittest.main()
