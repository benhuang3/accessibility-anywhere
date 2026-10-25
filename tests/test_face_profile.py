import json
import logging
import threading
import tempfile
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from ally.face_profile import apply, build
from ally.paths import Paths
from ally.profile_store import MAX_BYTES, ProfileStore
from ally.senses.detectors import Baseline


class FaceProfileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.paths = Paths(self.tmp.name)
        self.paths.state_dir.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def test_build_apply_round_trip(self):
        import numpy as np
        from ally import gaze_model

        gaze = {"version": 1, "features": "v1", "screen": [100, 80], "mean": [0] * 15,
                "std": [1] * 15, "coef": np.zeros((16, 2)).tolist(), "offset": [4, 5],
                "training_samples": [[[1], [2]]], "frame_features": [[1, 2]]}
        base = Baseline(.2, .4, samples=20).to_dict()
        profile = build(gaze, {"baseline": base, "personal": {"threshold": .3}}, meta={"screen": [100, 80]})
        self.assertNotIn("training_samples", profile["gaze"])
        self.assertNotIn("frame_features", profile["gaze"])

        class Gaze:
            screen = (100, 80)
            def send(self, _line): return True
        class Senses:
            baseline = None
            def _build(self): pass
        class Agent:
            gaze = Gaze()
            senses = Senses()
        Agent.gaze.calibration_path = self.paths.state_dir / "gaze_calibration.json"
        Agent.senses.baseline_path = self.paths.state_dir / "senses_baseline.json"
        self.assertEqual(len(apply(Agent(), profile)), 2)
        saved = gaze_model.load(Agent.gaze.calibration_path)
        self.assertEqual(gaze_model.predict(gaze, [0] * 9), gaze_model.predict(saved, [0] * 9))
        restored = json.loads(Agent.senses.baseline_path.read_text())
        self.assertEqual(restored["squint"], .2)
        self.assertEqual(restored["feature_stats"]["personal_model"]["threshold"], .3)

    def test_apply_skips_mismatched_screen(self):
        profile = build(gaze={"version": 1, "features": "v1", "screen": [100, 80]})
        class G:
            screen = (200, 100)
            def send(self, _line): return True
        class A: gaze = G(); senses = None
        A.gaze.calibration_path = self.paths.state_dir / "gaze.json"
        self.assertEqual(apply(A(), profile), [])
        self.assertFalse(A.gaze.calibration_path.exists())

    def test_build_rejects_empty_and_oversize(self):
        with self.assertRaises(ValueError): build()
        store = ProfileStore(self.paths, {})
        with self.assertRaisesRegex(ValueError, "64 KB"):
            store.save({"x": "a" * MAX_BYTES})

    def test_local_save_load_delete(self):
        store = ProfileStore(self.paths, {})
        profile = build(au={"baseline": {"blink": .2}})
        self.assertTrue(store.save(profile)["saved"])
        self.assertEqual(store.load(), profile)
        self.assertTrue(store.status()["saved"])
        store.delete()
        self.assertIsNone(store.load())

    def test_cloud_identity_call_sql_and_cache(self):
        token = "secret-identity-token-123456789"
        profile = build(au={"baseline": {"blink": .1}})
        calls = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args): pass
            def do_POST(self):
                size = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(size)
                calls.append((self.path, self.headers.get("Authorization"), body,
                              self.headers.get("Content-Type")))
                if self.path == "/v1/identity":
                    result = {"token": token}
                elif self.path.endswith("/sql"):
                    result = {"rows": [{"profile_json": json.dumps(profile)}]}
                else:
                    result = {"ok": True}
                data = json.dumps(result).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        store = ProfileStore(self.paths, {"profile": {"sync": "spacetimedb",
                                  "host": f"http://127.0.0.1:{server.server_port}"}})
        try:
            self.assertTrue(store.save(profile)["synced"])
            self.assertNotIn(token, store.path.read_text())
            store.path.unlink()
            self.assertEqual(store.load(), profile)
            store.delete()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=1)
        self.assertEqual(calls[0][0:1], ("/v1/identity",))
        self.assertEqual(json.loads(calls[0][2]), [])
        self.assertEqual(calls[1][0], "/v1/database/ally-call/call/save_face_profile")
        self.assertEqual(json.loads(calls[1][2])[0], json.dumps(profile, separators=(",", ":")))
        self.assertEqual(calls[1][1], "Bearer " + token)
        self.assertEqual(calls[2][0], "/v1/database/ally-call/sql")
        self.assertEqual(calls[2][2].decode(), "SELECT profile_json FROM own_face_profile")
        self.assertTrue(calls[2][3].startswith("text/plain"))
        self.assertEqual(calls[-1][0], "/v1/database/ally-call/call/delete_face_profile")
        self.assertNotIn(token, json.dumps(profile))

    def test_offline_fallback_keeps_local_and_never_logs_token(self):
        store = ProfileStore(self.paths, {"profile": {"sync": "spacetimedb"}})
        profile = build(au={"baseline": {"blink": .1}})
        with patch.object(store, "_request", side_effect=OSError("secret-identity-token-123456789")):
            with self.assertLogs("ally.profile", level=logging.WARNING) as logs:
                result = store.save(profile)
        self.assertFalse(result["synced"])
        self.assertEqual(store.load(), profile)
        self.assertNotIn("secret-identity-token", " ".join(logs.output))

    def test_privacy_keys_are_removed(self):
        profile = build(gaze={"screen": [1, 1], "coef": [1], "images": ["private"],
                              "training_samples": [1], "landmarks": [2]})
        encoded = json.dumps(profile).lower()
        for key in ("images", "landmarks", "training_samples"):
            self.assertNotIn(key, encoded)


if __name__ == "__main__":
    unittest.main()
