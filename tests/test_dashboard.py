from dataclasses import asdict
import http.client
import json
from pathlib import Path
from queue import Queue
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from controller.dashboard import DashboardServer, DashboardBridge, EventView, read_replay
from controller.contracts import Snapshot, RequestJob, DecisionResult


class ReplayTests(unittest.TestCase):
    def test_join_ids_across_reset_and_keep_raw_separate(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); run=root / "example"; run.mkdir()
            (run / "manifest.json").write_text(json.dumps({"mode":"mock","complete":False}))
            one=Snapshot("example",0,0,200,10,{"player":{"body_position":"airborne"}},
                         observation={"player":{"x":10,"y":120}})
            two=Snapshot("example",1,1,1,12,{"player":{"body_position":"grounded"}})
            events=[{"event":"request","job":asdict(RequestJob(1,one))},
                    {"event":"state","time":10,"episode":0,"frame":200,"state":{"opponent_character_id":3}},
                    {"event":"reset","time":11,"episode":1,"frame":0,"state":{"opponent_character_id":4}},
                    {"event":"request","job":asdict(RequestJob(2,two))},
                    {"event":"result","result":asdict(DecisionResult(1,one,10,12,12,"ok",action="sweep"))},
                    {"event":"discarded","request_id":1,"reason":"old_state"},
                    {"event":"applied","request_id":2,"frame":20,"action":"sweep","strength":"heavy"},
                    {"event":"inputs_released","frame":25,"time":13},
                    {"event":"round_finished","time":14,"health":0,"enemy_health":-1}]
            (run / "events.jsonl").write_text("\n".join(json.dumps(e) for e in events)+'\n{"unfinished":')
            result=read_replay(root,"example")
            self.assertEqual(result["skipped"],1)
            self.assertEqual([r["disposition"] for r in result["rows"]],["discarded: old_state","applied"])
            self.assertNotIn("x",result["rows"][0]["snapshot"]["state"]["player"])
            self.assertEqual(result["rows"][0]["snapshot"]["observation"]["player"]["x"],10)
            self.assertEqual([s["frame"] for s in result["frames"]],[200,0])
            self.assertEqual(result["buttons"][0]["held"],[])
            self.assertEqual(result["rounds"][0]["opponent"],"Ken")

    def test_replay_rejects_traversal_and_symlink(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)/"runs"; root.mkdir()
            (root / "outside").symlink_to(Path(folder))
            for ident in ("../secret","outside",".."):
                with self.assertRaises(ValueError):read_replay(root,ident)

    def test_bounded_live_history_keeps_applied_strength(self):
        view=EventView(limit=2)
        for ident in range(5):
            view.record("applied",{"request_id":ident,"action":"sweep","strength":"heavy"})
        self.assertEqual(list(view.rows),[3,4])
        self.assertEqual(view.rows[4]["execution"]["strength"],"heavy")

    def test_commands_are_acknowledged_by_real_app_state(self):
        bridge=DashboardBridge.__new__(DashboardBridge)
        bridge.commands=Queue();bridge.ready=Queue();bridge.last_sent=100;bridge.ack=None
        bridge.process=SimpleNamespace(is_alive=lambda:True)
        app=SimpleNamespace(recorder=SimpleNamespace(run_id="current",emit=Mock()),closing=False,
                            paused=True,reason="Worker not ready",resume=Mock(),pause=Mock(),begin_close=Mock())
        bridge.commands.put({"id":"old","run_id":"other","command":"stop"})
        bridge.commands.put({"id":"new","run_id":"current","command":"resume"})
        bridge.poll(app)
        app.begin_close.assert_not_called();app.resume.assert_called_once()
        self.assertEqual(bridge.ack,{"id":"new","accepted":False,"reason":"Worker not ready"})
        self.assertEqual(bridge.last_sent,0)


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.commands=Queue(maxsize=1)
        self.server=DashboardServer(Path(self.temp.name),self.commands)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.server.latest={"run_id":"current","sent_at":time.time()}

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join();self.temp.cleanup()

    def request(self,path="/api/control",value=None,headers=None):
        client=http.client.HTTPConnection("127.0.0.1",self.server.server_port,timeout=2)
        try:
            client.request("GET" if value is None else "POST",path,None if value is None else json.dumps(value),headers or {})
            response=client.getresponse();return response.status,response.read()
        finally:client.close()

    def test_controls_require_token_origin_matching_run_and_recent_feed(self):
        value={"id":"1","run_id":"current","command":"pause"}
        headers={"X-Dashboard-Token":self.server.token}
        self.assertEqual(self.request(value=value)[0],403)
        self.assertEqual(self.request(value=value,headers={**headers,"Origin":"https://example.com"})[0],403)
        self.assertEqual(self.request(value={**value,"run_id":"old"},headers=headers)[0],409)
        self.server.latest["sent_at"]=time.time()-10
        self.assertEqual(self.request(value=value,headers=headers)[0],409)
        self.server.latest["sent_at"]=time.time()
        self.assertEqual(self.request(value=value,headers=headers)[0],202)
        self.assertEqual(self.request(value=value,headers=headers)[0],429)
        self.assertEqual(self.commands.get_nowait(),value)

    def test_static_allowlist_host_and_replay_no_model(self):
        status,body=self.request("/");self.assertEqual(status,200);self.assertIn(b"Ringside",body)
        self.assertEqual(self.request("/.env")[0],404)
        self.assertEqual(self.request("/api/runs/../../.env")[0],404)
        self.assertEqual(self.request("/api/live",headers={"Host":"attacker.invalid"})[0],403)
        self.assertEqual(self.request("/api/runs")[0],200)

    def test_video_is_same_origin_and_bound_to_the_requested_run(self):
        self.assertEqual(self.request("/api/video?run=current")[0],204)
        self.server.video={"run_id":"current","frame":15,"episode":2,"captured":1.5,
                           "held":["DOWN","Y"],"phase":"entering_buttons","png":b"PNG fixture"}
        self.assertEqual(self.request("/api/video?run=other")[0],409)
        self.assertEqual(self.request("/api/video?run=current",headers={"Origin":"https://example.com"})[0],403)
        client=http.client.HTTPConnection("127.0.0.1",self.server.server_port,timeout=2)
        try:
            client.request("GET","/api/video?run=current")
            response=client.getresponse()
            self.assertEqual(response.status,200)
            self.assertEqual(response.getheader("Content-Type"),"image/png")
            self.assertEqual(response.getheader("X-Game-Frame"),"15")
            self.assertEqual(response.getheader("X-Buttons"),"DOWN,Y")
            self.assertEqual(response.read(),b"PNG fixture")
        finally:
            client.close()


if __name__ == "__main__":
    unittest.main()
