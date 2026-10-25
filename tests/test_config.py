import time
import unittest

from helpers import temp_home

from ally import keys
from ally.config import DEFAULTS, ConfigManager, merge, parse, validate


def check(text: str, tools=frozenset({"set_page_zoom", "restyle_page"})):
    cm = ConfigManager(temp_home() / "config.toml")
    return cm.check_text(text, set(tools))


class ConfigTests(unittest.TestCase):
    def test_defaults_are_valid(self):
        errors, warnings = validate(merge({}), set())
        self.assertEqual(errors, [])
        self.assertEqual(warnings, [])
        self.assertIn("high_contrast", DEFAULTS["page_styles"])

    def test_personal_squint_method_is_allowed_and_v1_stays_default(self):
        self.assertEqual(DEFAULTS["senses"]["squint_method"], "v1")
        config = merge({"senses": {"squint_method": "personal"}})
        errors, _warnings = validate(config, set())
        self.assertFalse([error for error in errors if "senses.squint_method" in error])

    def test_syntax_error_reported(self):
        cfg, errors, _ = check("[agent\nname = 'x'")
        self.assertIsNone(cfg)
        self.assertIn("syntax", errors[0])

    def test_type_and_range_errors(self):
        cfg, errors, _ = check('[agent]\nport = "abc"\n[speech]\nrate = 9.0\n')
        self.assertIsNone(cfg)
        self.assertTrue(any("agent.port" in e for e in errors))
        self.assertTrue(any("speech.rate" in e for e in errors))

    def test_unknown_key_is_warning_only(self):
        cfg, errors, warnings = check("[agent]\nnmae = 'typo'\n")
        self.assertIsNotNone(cfg)
        self.assertEqual(errors, [])
        self.assertTrue(any("agent.nmae" in w for w in warnings))

    def test_bad_hotkeys(self):
        _, errors, _ = check('[hotkeys]\n"<ctrl>+<bogus>" = "zoom_in"\n"r" = "zoom_in"\n')
        self.assertTrue(any("bogus" in e for e in errors))
        self.assertTrue(any("modifier" in e for e in errors))

    def test_hotkeys_table_replaces_defaults(self):
        cfg, errors, _ = check('[hotkeys]\n"<mod>+z" = "zoom_in"\n')
        self.assertEqual(errors, [])
        self.assertEqual(list(cfg["hotkeys"]), ["<mod>+z"])

    def test_duplicate_hotkey_with_command(self):
        text = ('[hotkeys]\n"<mod>+1" = "zoom_in"\n[commands.x]\nhotkey = "<mod>+1"\n'
                'steps = [{ say = "hi" }]\n')
        _, errors, _ = check(text)
        self.assertTrue(any("used twice" in e for e in errors))

    def test_disabled_hotkey_frees_combo(self):
        text = ('[hotkeys]\n"<mod>+1" = ""\n[commands.x]\nhotkey = "<mod>+1"\nsteps = [{ say = "hi" }]\n')
        cfg, errors, _ = check(text)
        self.assertEqual(errors, [])

    def test_command_steps(self):
        text = ('[commands.bad]\nsteps = [{ say = "a", shell = "b" }, { tool = "nope" }, { wait = 99 }]\n'
                '[commands.set_page_zoom]\nsteps = [{ say = "x" }]\n')
        _, errors, warnings = check(text)
        self.assertTrue(any("exactly one" in e for e in errors))
        self.assertTrue(any("wait" in e for e in errors))
        self.assertTrue(any("already a built-in" in e for e in errors))
        self.assertTrue(any("nope" in w for w in warnings))

    def test_os_specific_shell(self):
        cfg, errors, _ = check('[commands.calc]\nsteps = [{ shell = { mac = "open -a Calculator", windows = "calc" } }]\n')
        self.assertEqual(errors, [])
        _, errors, _ = check('[commands.calc]\nsteps = [{ shell = { amiga = "calc" } }]\n')
        self.assertTrue(errors)

    def test_profile_style_must_exist(self):
        _, errors, _ = check('[profile]\npage_styles = ["sparkles"]\n')
        self.assertTrue(any("sparkles" in e for e in errors))

    def test_manager_keeps_last_good_and_hot_reloads(self):
        home = temp_home()
        (home / "config.toml").write_text('[agent]\nname = "One"\n', encoding="utf-8")
        cm = ConfigManager(home / "config.toml")
        self.assertTrue(cm.reload())
        self.assertEqual(cm.data["agent"]["name"], "One")
        time.sleep(0.01)
        (home / "config.toml").write_text("[agent\nbroken", encoding="utf-8")
        import os
        os.utime(home / "config.toml", (time.time() + 5, time.time() + 5))
        self.assertTrue(cm.changed_on_disk())
        self.assertFalse(cm.reload())
        self.assertEqual(cm.data["agent"]["name"], "One")  # still the last good settings
        self.assertIsNotNone(cm.error)
        ok, errors, _ = cm.save_text('[agent]\nname = "Two"\n')
        self.assertTrue(ok, errors)
        self.assertEqual(cm.data["agent"]["name"], "Two")
        self.assertIsNone(cm.error)
        ok, errors, _ = cm.save_text("[agent]\nport = 1\n")
        self.assertFalse(ok)
        self.assertIn('name = "Two"', (home / "config.toml").read_text())  # bad save never hits disk

    def test_parse_roundtrip_of_default_file(self):
        from ally.config import DEFAULT_TEXT

        self.assertEqual(parse(DEFAULT_TEXT)["agent"]["port"], 8765)


class KeyTests(unittest.TestCase):
    def test_split_like_pynput(self):
        self.assertEqual(keys.split_combo("<ctrl>+<alt>+r"), ["<ctrl>", "<alt>", "r"])
        self.assertEqual(keys.split_combo("<ctrl>++"), ["<ctrl>", "+"])
        with self.assertRaises(ValueError):
            keys.split_combo("<ctrl>+")

    def test_mod_expansion(self):
        self.assertEqual(keys.expand_mod("<mod>+r", "Darwin"), "<ctrl>+<cmd>+r")
        self.assertEqual(keys.expand_mod("<mod>+r", "Windows"), "<ctrl>+<alt>+r")

    def test_parse_combo(self):
        self.assertEqual(keys.parse_combo("<mod>+=", "Linux"), ["<ctrl>", "<alt>", "="])
        self.assertEqual(keys.parse_combo("<f13>", "Linux"), ["<f13>"])
        for bad in ("<ctrl>+<alt>", "<ctrl>+<ctrl>+a", "a", "<f1>", "<ctrl>+ab"):
            with self.assertRaises(ValueError, msg=bad):
                keys.parse_combo(bad, "Linux")

    def test_describe(self):
        self.assertEqual(keys.describe("<mod>+r", "Windows"), "Ctrl+Alt+R")
        self.assertEqual(keys.describe("<mod>+r", "Darwin"), "Ctrl+Cmd+R")


if __name__ == "__main__":
    unittest.main()
