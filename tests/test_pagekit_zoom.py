"""choose_zoom: picks a Chrome zoom from an audit of CSS-pixel measurements (no browser needed)."""

import unittest

import helpers  # noqa: F401  (puts the repo on sys.path)
from ally.pagekit.zoom import choose_zoom


def level(zoom, vw, text_px=16, **kw):
    entry = {"zoom": zoom, "vw": vw, "vh": 800, "textPx": text_px, "textSizes": [[text_px, 1.0]],
             "overflowX": False, "overflowPx": 0, "cutOffText": 0, "clipped": [], "stickyCover": 0.08,
             "covered": [], "smallTargets": [], "targets": 10}
    entry.update(kw)
    return entry


def responsive(levels=(1, 1.1, 1.25, 1.5, 1.75, 2, 2.5), width=1600):
    return [level(z, round(width / z)) for z in levels]


def audit(entries, original=1, **kw):
    return {"tabId": 1, "url": "https://example.com/", "original": original, "defaultZoom": 1, "audits": entries, **kw}


class ChooseZoomTest(unittest.TestCase):
    def test_responsive_site_passes_at_1_5(self):
        # 16px text: 1.25 -> 20px meets a 20px goal; 24px goal needs 1.5.
        r = choose_zoom(audit(responsive()), {"text_px": 24})
        self.assertEqual(r["zoom"], 1.5)
        self.assertTrue(r["met"])
        self.assertFalse(r["reader_suggested"])

    def test_already_big_enough_keeps_original(self):
        r = choose_zoom(audit(responsive()), {"text_px": 14})
        self.assertEqual(r["zoom"], 1)
        self.assertTrue(r["met"])

    def test_fixed_width_site_backs_off(self):
        # A fixed 960px layout overflows once the viewport is narrower than 960 CSS px.
        entries = []
        for z in (1, 1.1, 1.25, 1.5, 1.75, 2):
            vw = round(1280 / z)
            over = max(0, 960 - vw)
            entries.append(level(z, vw, overflowX=over > 0, overflowPx=over, cutOffText=3 if over > 8 else 0))
        r = choose_zoom(audit(entries), {"text_px": 24})
        self.assertEqual(r["zoom"], 1.25)
        self.assertFalse(r["met"])
        self.assertFalse(r["reader_suggested"])
        self.assertEqual(r["safe_levels"], [1, 1.1, 1.25])

    def test_article_suggests_reader(self):
        entries = [level(1, 1280), level(1.25, 1024, overflowPx=300, cutOffText=5)]
        r = choose_zoom(audit(entries, article=True), {"text_px": 24})
        self.assertEqual(r["zoom"], 1)
        self.assertFalse(r["met"])
        self.assertTrue(r["reader_suggested"])

    def test_big_sticky_header_excluded(self):
        entries = responsive()
        for e in entries:
            if e["zoom"] >= 2:
                e["stickyCover"] = 0.4
        r = choose_zoom(audit(entries), {"text_px": 32})
        self.assertNotIn(2, r["safe_levels"])
        self.assertEqual(r["zoom"], 1.75)
        self.assertFalse(r["met"])

    def test_new_clipped_and_covered(self):
        entries = responsive(width=2400)
        entries[3]["clipped"] = ["div:nth-of-type(2)"]  # 1.5
        entries[4]["covered"] = ["#a", "#b"]  # 1.75
        entries[5]["covered"] = ["#a"]  # 2: one new covered target is tolerated
        r = choose_zoom(audit(entries), {"text_px": 24})
        self.assertEqual(r["zoom"], 2)
        self.assertNotIn(1.5, r["safe_levels"])
        self.assertNotIn(1.75, r["safe_levels"])

    def test_tablet_squeeze_prefers_phone_layout(self):
        # 1600 wide: 1.75 -> 914px (squeeze band), 2.5 -> 640px (phone layout).
        r = choose_zoom(audit(responsive()), {"text_px": 28})
        self.assertEqual(r["zoom"], 2.5)
        self.assertTrue(r["phone_layout"])
        self.assertTrue(r["met"])

    def test_no_phone_preference_outside_squeeze(self):
        r = choose_zoom(audit(responsive(width=2400)), {"text_px": 24})
        self.assertEqual(r["zoom"], 1.5)
        self.assertFalse(r["phone_layout"])

    def test_small_text_share(self):
        # 30% of the text is 12px: at 1.5 that is 18px, still under a 20px goal; at 2 it is 24px.
        entries = responsive(width=2400)
        for e in entries:
            e["textSizes"] = [[12, 0.3], [16, 1.0]]
        r = choose_zoom(audit(entries), {"text_px": 20})
        self.assertEqual(r["zoom"], 1.75)

    def test_buttons_aim(self):
        entries = responsive(width=2400)
        small = [{"key": f"a{i}", "w": 60, "h": 24} for i in range(5)]
        for e in entries:
            e["smallTargets"] = small
        # 5 of 10 targets are 24px tall: need 24 * zoom >= 44 for 80% -> 2.0 (1.75 gives 42).
        r = choose_zoom(audit(entries), {"aim": "buttons", "target_px": 44})
        self.assertEqual(r["zoom"], 2)
        self.assertTrue(r["met"])

    def test_max_zoom_respected(self):
        r = choose_zoom(audit(responsive()), {"text_px": 40, "max_zoom": 1.5})
        self.assertLessEqual(r["zoom"], 1.5)
        self.assertEqual(r["zoom"], 1.5)
        self.assertFalse(r["met"])
        self.assertNotIn(2, r["safe_levels"])

    def test_no_safe_level_returns_original(self):
        entries = [level(1.1, 1160)] + [level(z, round(1280 / z), overflowPx=200) for z in (1.25, 1.5, 2)]
        entries[0]["zoom"] = 1.1
        r = choose_zoom(audit(entries, original=1.1), {"text_px": 30})
        self.assertEqual(r["zoom"], 1.1)
        self.assertFalse(r["met"])
        self.assertEqual(r["safe_levels"], [1.1])

    def test_empty_audit(self):
        r = choose_zoom(audit([], original=1.25), {})
        self.assertEqual(r["zoom"], 1.25)
        self.assertFalse(r["met"])

    def test_missing_baseline_uses_lowest(self):
        entries = responsive(levels=(1.1, 1.25, 1.5))
        r = choose_zoom(audit(entries, original=None), {"text_px": 20})
        self.assertEqual(r["zoom"], 1.25)


if __name__ == "__main__":
    unittest.main()
