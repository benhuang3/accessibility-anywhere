import ast
from dataclasses import FrozenInstanceError
import inspect
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from helpers import ROOT

from ally.risk.process import REMOTE_TOOLS, RemoteEvent, RemoteWatcher, match_remote_tool, snapshot_processes


class RemoteToolTests(unittest.TestCase):
    def test_matching(self):
        for name, label in (
            ("AnyDesk.exe", "AnyDesk"), ("ANYDESK", "AnyDesk"),
            ("TeamViewer_Service.exe", "TeamViewer"), ("Quick Assist", "Quick Assist"),
            ("QuickAssist.exe", "Quick Assist"), ("rustdesk.exe", "RustDesk"),
            ("Supremo.exe", "Supremo"), ("client32.exe", "NetSupport"),
            ("ScreenConnect.ClientService.exe", "ScreenConnect"),
            ("  teamviewer-desktop.EXE  ", "TeamViewer"),
            ("TeamViewer Service", "TeamViewer")):
            with self.subTest(name=name):
                self.assertEqual(match_remote_tool(name), label)
        self.assertEqual(match_remote_tool("renamed.exe", "AnyDesk.exe"), "AnyDesk")
        for label, aliases in REMOTE_TOOLS.items():
            for alias in aliases:
                self.assertEqual(match_remote_tool(alias + ".exe"), label)

    def test_nonmatches(self):
        for name in ("anydeskhelper", "teamviewerfan", "client3200", "chrome", "python",
                     "svchost", "explorer", "", "  ", None, 42, [], {}, b"anydesk"):
            with self.subTest(name=name):
                self.assertIsNone(match_remote_tool(name))
                self.assertIsNone(match_remote_tool("unknown", name))


class RemoteWatcherTests(unittest.TestCase):
    def test_baseline(self):
        watcher = RemoteWatcher()
        processes = [{"name": "AnyDesk.exe"}]
        event, = watcher.observe(processes, 100)
        self.assertIsInstance(event, RemoteEvent)
        self.assertEqual((event.tool, event.process, event.started, event.severity),
                         ("AnyDesk", "AnyDesk.exe", False, "medium"))
        self.assertEqual(watcher.observe(processes, 101), [])
        with self.assertRaises(FrozenInstanceError):
            event.started = True

    def test_start_stay_exit_and_return(self):
        watcher = RemoteWatcher()
        self.assertEqual(watcher.observe([{"name": "chrome"}], 0), [])
        processes = [{"name": "AnyDesk.exe"}]
        event, = watcher.observe(processes, 1)
        self.assertTrue(event.started)
        self.assertEqual(event.severity, "medium")
        self.assertEqual(watcher.observe(processes, 2), [])
        self.assertEqual(watcher.observe([], 3), [])
        self.assertTrue(watcher.observe(processes, 4)[0].started)

    def test_severity_and_boundaries(self):
        for payment, scam_at, expected in (
            (True, None, "high"), (False, 100, "high"), (False, 700, "high"),
            (False, 99.9, "medium"), (False, 701, "medium"), (False, None, "medium")):
            with self.subTest(payment=payment, scam_at=scam_at):
                watcher = RemoteWatcher()
                watcher.observe([], 0)
                event, = watcher.observe([{"name": "AnyDesk"}], 700, scam_at, payment)
                self.assertEqual(event.severity, expected)
        watcher = RemoteWatcher(high_window_s=10)
        watcher.observe([], 0)
        self.assertEqual(watcher.observe([{"name": "rustdesk"}], 11, 0)[0].severity, "medium")

    def test_baseline_context(self):
        for payment in (True, False):
            event, = RemoteWatcher().observe([{"name": "AnyDesk"}], 100, 99, payment)
            self.assertFalse(event.started)
            self.assertEqual(event.severity, "high" if payment else "medium")

    def test_junk_and_duplicate_names(self):
        junk = [None, 42, [], "anydesk", {}, {"name": None}, {"name": 42},
                {"name": []}, {"name": {}}, {"name": " "},
                {"name": "unknown", "original_filename": []}]
        watcher = RemoteWatcher()
        self.assertEqual(watcher.observe(junk, 0), [])
        events = watcher.observe(junk + [{"name": "AnyDesk.exe"}, {"name": "ANYDESK"},
                                        {"name": "renamed", "original_filename": "rustdesk.exe"}], 1)
        self.assertEqual([event.tool for event in events], ["AnyDesk", "RustDesk"])
        self.assertEqual(watcher.observe([{"name": " anydesk "}, {"name": "renamed"}], 2), [])

    def test_determinism_and_evidence(self):
        inputs = [([{"name": "client32.exe"}], 0, None, False),
                  ([], 1, None, False),
                  ([{"name": "client32.exe"}], 2, None, False),
                  ([], 3, None, False),
                  ([{"name": "client32.exe"}], 4, None, True),
                  ([], 5, None, False),
                  ([{"name": "client32.exe"}], 6, 5, False)]
        def run():
            watcher = RemoteWatcher()
            return [watcher.observe(*args) for args in inputs]
        first = run()
        self.assertEqual(first, run())
        evidence = []
        for events in first:
            for event in events:
                self.assertFalse(any(c.isdigit() for c in event.evidence))
                self.assertIn(event.tool, event.evidence)
                self.assertIn("whether you started it", event.evidence)
                self.assertEqual(event.evidence.count("."), 1)
                evidence.append(event.evidence)
        self.assertEqual(len(set(evidence)), 4)

    def test_no_ally_imports_and_namespace(self):
        source = inspect.getsource(inspect.getmodule(match_remote_tool))
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                self.assertFalse(any(n.name.split(".")[0] == "ally" for n in node.names))
            elif isinstance(node, ast.ImportFrom):
                self.assertNotEqual((node.module or "").split(".")[0], "ally")
                self.assertEqual(node.level, 0)


class SnapshotTests(unittest.TestCase):
    def test_psutil_names_and_inaccessible_process(self):
        class AccessDenied(Exception):
            pass

        class NoSuchProcess(Exception):
            pass

        class Inaccessible:
            @property
            def info(self):
                raise AccessDenied()

        class Gone:
            @property
            def info(self):
                raise NoSuchProcess()

        fake = SimpleNamespace(AccessDenied=AccessDenied, NoSuchProcess=NoSuchProcess,
                               process_iter=Mock(return_value=[
                                   SimpleNamespace(info={"name": "AnyDesk.exe", "exe": "private path"}),
                                   Inaccessible(), Gone(), SimpleNamespace(info={"name": None}),
                                   SimpleNamespace(info={"name": "chrome.exe"})]))
        with patch.dict(sys.modules, {"psutil": fake}):
            self.assertEqual(snapshot_processes(), [{"name": "AnyDesk.exe"}, {"name": "chrome.exe"}])
        fake.process_iter.assert_called_once_with(["name", "exe"])

    def test_missing_psutil_nonwindows(self):
        with patch.dict(sys.modules, {"psutil": None}), patch("sys.platform", "linux"), \
                patch("subprocess.run") as run:
            self.assertEqual(snapshot_processes(), [])
            run.assert_not_called()

    def test_tasklist_fallback_and_errors(self):
        with patch.dict(sys.modules, {"psutil": None}), patch("sys.platform", "win32"), \
                patch("subprocess.CREATE_NO_WINDOW", 0x08000000, create=True), \
                patch("subprocess.run") as run:
            run.return_value.stdout = '"AnyDesk.exe","123","Console","1","5 K"\n'
            self.assertEqual(snapshot_processes(), [{"name": "AnyDesk.exe"}])
            self.assertEqual(run.call_args.kwargs["timeout"], 5)
            self.assertEqual(run.call_args.kwargs["creationflags"], 0x08000000)
            run.side_effect = OSError("unavailable")
            self.assertEqual(snapshot_processes(), [])

    def test_psutil_failure_never_raises(self):
        fake = SimpleNamespace(process_iter=Mock(side_effect=RuntimeError("unavailable")))
        with patch.dict(sys.modules, {"psutil": fake}):
            self.assertEqual(snapshot_processes(), [])


class OneAlertPerToolTests(unittest.TestCase):
    def test_two_processes_of_one_tool_alert_once(self):
        w = RemoteWatcher()
        w.observe([{"name": "chrome.exe"}], now=0)
        events = w.observe([{"name": "chrome.exe"}, {"name": "TeamViewer.exe"},
                            {"name": "TeamViewer_Service.exe"}], now=1)
        self.assertEqual([e.tool for e in events], ["TeamViewer"])

    def test_zoho_meeting_is_not_a_remote_tool(self):
        self.assertIsNone(match_remote_tool("ZohoMeeting.exe"))
        self.assertEqual(match_remote_tool("ZohoAssist.exe"), "Zoho Assist")


class CatalogueTests(unittest.TestCase):
    """remote_tools.json comes from LOLRMM (Apache-2.0); the hand-checked list wins where both know a name."""

    def test_catalogue_adds_tools_beyond_the_hand_list(self):
        from ally.risk.process import known_tool
        self.assertEqual(match_remote_tool("AA_v3.exe"), "Ammyy Admin")
        self.assertEqual(match_remote_tool("RemotePC.exe"), "RemotePC")
        self.assertEqual(match_remote_tool("AnyDesk.exe"), "AnyDesk")   # hand list label
        self.assertTrue(known_tool("RemotePC"))
        self.assertFalse(known_tool("chrome"))

    def test_everyday_programs_never_match(self):
        for name in ("chrome.exe", "msedge.exe", "explorer.exe", "svchost.exe", "Code.exe", "connect.exe", "client.exe",
                     "setup.exe", "Zoom.exe", "Teams.exe", "ZohoMeeting.exe", "OneDrive.exe", "python.exe",
                     "nvda.exe", "Narrator.exe", "Magnify.exe", "jfw.exe", "ZoomText.exe"):  # assistive tech is never a target
            self.assertIsNone(match_remote_tool(name), name)


if __name__ == "__main__":
    unittest.main()
