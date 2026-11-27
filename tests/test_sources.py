"""检查演练来源和本地回放端。"""
import json
import threading
import unittest
from urllib.request import urlopen

from replay import ROOT, ReplayHandler, ThreadingHTTPServer


class SourceTests(unittest.TestCase):
    def setUp(self):
        self.data = {p.stem: json.loads(p.read_bytes()) for p in (ROOT / "data").glob("*.json")}

    def test_replay_source(self):
        with ThreadingHTTPServer(("127.0.0.1", 0), ReplayHandler) as server:
            worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02})
            worker.start()
            try:
                prefix = "http://127.0.0.1:" + str(server.server_port)
                with urlopen(prefix + "/streams", timeout=5) as response:
                    self.assertEqual(set(json.load(response)), set(self.data))
                first = self.data["timeline"][0]
                with urlopen(prefix + "/timeline?through=" + str(first["at"]), timeout=5) as response:
                    events = json.load(response)
                self.assertIn(first, events)
                self.assertLess(len(events), len(self.data["timeline"]))
            finally:
                server.shutdown()
                worker.join()

    def test_source_relationships(self):
        d = self.data
        origin = {r["local_id"] for r in d["directory"] if r["subject"] == "old-81"}
        self.assertEqual(len(origin), 2)
        self.assertTrue(any(set(r["candidates"]) == origin for r in d["evidence"]))
        self.assertTrue(any(not r["candidates"] for r in d["evidence"]))
        self.assertTrue(any(set(r["candidates"]) != origin and set(r["candidates"]) < origin
                            and r["candidates"] for r in d["evidence"]))


if __name__ == "__main__":
    unittest.main()
