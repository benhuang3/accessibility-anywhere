import json
import math
import tempfile
import unittest
from pathlib import Path
# Adds the repository root to sys.path for these tests.
import helpers
import numpy as np
from ally import gaze_model as gm
from ally import gaze_eval

class GazeModelTests(unittest.TestCase):

    def test_gaze_smoother_rejects_outliers_and_holds_missing(self):
        smooth = gm.GazeSmoother()
        shown = [smooth.update((100 + (-1) ** i * 8, 200), i * 0.08, 100) for i in range(10)]
        before = smooth.update((600, 700), 0.81, 100)
        after = smooth.update((101, 200), 0.89, 100)
        held = smooth.update(None, 0.97, 100)
        self.assertLess(np.max(np.linalg.norm(np.diff(shown, axis=0), axis=1)), 3)
        self.assertLess(np.linalg.norm(before - shown[-1]), 3)
        np.testing.assert_allclose(held, after)

    def test_gaze_smoother_follows_saccade_within_250ms(self):
        smooth = gm.GazeSmoother()
        smooth.update((100, 100), 0.0, 100)
        smooth.update((110, 102), 0.08, 100)
        smooth.update((500, 400), 0.16, 100)
        smooth.update((505, 402), 0.24, 100)
        shown = smooth.update((503, 401), 0.32, 100)
        self.assertLess(np.linalg.norm(shown - (503, 401)), 10)

    def test_gaze_smoother_reset_reinitializes_after_missing_frames(self):
        smooth = gm.GazeSmoother()
        smooth.update((100, 100), 0.0, 80)
        held = smooth.update(None, 0.1, 80)
        np.testing.assert_allclose(held, (100, 100))
        smooth.reset()
        shown = smooth.update((800, 600), 0.2, 80)
        np.testing.assert_allclose(shown, (800, 600))

    def landmarks(self, rh=0, rv=0, lh=0, lv=0):
        a = np.zeros((478, 3), float)
        for ids, h, v in ((gm.RIGHT_EYE, rh, rv), (gm.LEFT_EYE, lh, lv)):
            a[ids['inner'], :2] = [0.4, 0.5]
            a[ids['outer'], :2] = [0.6, 0.5]
            a[ids['top'], :2] = [0.5, 0.48]
            a[ids['bottom'], :2] = [0.5, 0.52]
            a[ids['iris'], :2] = [0.5 + h * 0.2, 0.5 + v * 0.2]
        return a

    def test_v3_ray_intersects_known_screen_point(self):
        hit = gm.ray_screen_point((0, 0, 40), (0.1, 0.05, -1), (344, 193), (1920, 1080), -1)
        self.assertAlmostEqual(hit[0], 1188.8372, delta=0.1)
        self.assertAlmostEqual(hit[1], 114.7150, delta=0.1)

    def test_v3_geometric_gaze_is_stable_across_head_pose(self):
        target = np.array([0.08, 0.09])
        vectors = []
        v1 = []
        for yaw, tx, tz in ((0, 0, -40), (math.radians(12), 4, -45)):
            c, s = math.cos(yaw), math.sin(yaw)
            rot = np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])
            trans = np.array([tx, 0, tz])
            lm = self.landmarks()
            for side, ids in ((-1, gm.RIGHT_EYE), (1, gm.LEFT_EYE)):
                eye = trans + rot @ np.array([side * 3.2, 0, 0])
                desired = np.array([(target[0] - .5) * 34.4, target[1] * 19.3, 0]) - eye
                local = rot.T @ desired
                h, v = local[0] / local[2] / 2.5, local[1] / local[2] / 2.5
                lm[ids['iris'], 0] = .5 + h * .2
                lm[ids['iris'], 1] = .5 + v * .2
            mat = np.eye(4)
            mat[:3, :3], mat[:3, 3] = rot, trans
            v3 = gm.features_for_model({'features': 'v3'}, lm, {}, mat, 640, 480)[0]
            v1.append(gm.features(lm, {}, mat, 640, 480)[0])
            vectors.append(v3[:2])
        self.assertLess(np.linalg.norm(np.asarray(vectors[0]) - vectors[1]), .02)
        self.assertGreater(np.linalg.norm(np.asarray(v1[0][4:6]) - v1[1][4:6]), math.radians(10))

    def test_v3_fit_recovers_known_screen_mapping(self):
        samples = []
        for x, y in ((100, 100), (960, 100), (1820, 100), (100, 540), (960, 540),
                      (1820, 540), (100, 980), (960, 980), (1820, 980)):
            f = [x / 1920, y / 1080, 0, 0, 0, 0, 0, 0, 40, 0]
            samples.append((f, (x, y)))
        model = gm.fit(samples, (1920, 1080), 'v3')
        for f, target in samples:
            self.assertLess(np.linalg.norm(np.asarray(gm.predict(model, f)) - target), 50)

    def test_per_eye_feature_directions(self):
        zero = gm.features_for_model({'features': 'v2'}, self.landmarks(), {}, None, 640, 480)[0]
        moved = gm.features_for_model({'features': 'v2'}, self.landmarks(0.1, 0.1, 0.1, 0.1), {'eyeLookUpLeft': 0.7, 'eyeLookUpRight': 0.6}, None, 640, 480)[0]
        self.assertGreater(moved[0], zero[0])
        self.assertGreater(moved[2], zero[2])
        self.assertGreater(moved[1], zero[1])
        self.assertGreater(moved[3], zero[3])
        self.assertGreater(moved[6], 0)

    def test_v2_lid_feature_names_match_vector(self):
        blendshapes = {'eyeBlinkLeft': 0.2, 'eyeBlinkRight': 0.6}
        landmarks = self.landmarks()
        landmarks[gm.LEFT_EYE['top'], 1] = 0.47
        landmarks[gm.LEFT_EYE['bottom'], 1] = 0.53
        vector = gm.features_for_model({'features': 'v2'}, landmarks, blendshapes, None, 640, 480)[0]
        values = dict(zip(gm.FEATURES_V2, vector))
        self.assertAlmostEqual(values['left_lid'], 0.225)
        self.assertAlmostEqual(values['right_lid'], 0.15)

    def test_fit_validate_region_and_radius(self):
        samples = []
        for x, y in [(100, 100), (960, 100), (1820, 100), (100, 540), (960, 540), (1820, 540), (100, 980), (960, 980), (1820, 980)]:
            f = [x / 1920, y / 1080, 0, 0, 0, 0, 0, 0, 1]
            samples.append((f, (x, y)))
        m = gm.fit(samples, (1920, 1080))
        self.assertLess(gm.validate(m, samples)['error_px'], 40)
        self.assertEqual(gm.region(10, 10, (1920, 1080)), 'top left')
        self.assertEqual(gm.region(960, 540, (1920, 1080)), 'middle')
        baseline = [0, 0, 0, 0, 0.5, 0.5, 0, 0, -50]
        realistic = [(baseline[:4] + [x / 1920, y / 1080] + baseline[6:], (x, y)) for x, y in [(100, 100), (960, 100), (1820, 100), (100, 540), (960, 540), (1820, 540), (100, 980), (960, 980), (1820, 980)]]
        pose_model = gm.fit(realistic, (1920, 1080))
        base_radius = gm.radius_px(pose_model, baseline)
        turned = baseline.copy()
        turned[4] = np.deg2rad(10)
        closer = baseline.copy()
        closer[8] = -42.5
        self.assertEqual(gm.radius_px(pose_model, baseline), base_radius)
        self.assertGreater(gm.radius_px(pose_model, turned), base_radius)
        self.assertGreater(gm.radius_px(pose_model, closer), base_radius)
        self.assertLessEqual(gm.radius_px(pose_model, turned), 3 * base_radius)
        # A 1 cm sideways shift at 50 cm is small: tx is a position, not an angle (v1 has no roll).
        shifted = baseline.copy()
        shifted[6] = 1.0
        self.assertLess(gm.radius_px(pose_model, shifted), 1.2 * base_radius)

    def test_prediction_is_clamped_to_screen_margin(self):
        samples = [([x / 1920, y / 1080, 0, 0, 0, 0, 0, 0, 1], (x, y))
                   for x, y in [(100, 100), (960, 100), (1820, 100), (100, 540), (960, 540),
                                (1820, 540), (100, 980), (960, 980), (1820, 980)]]
        model = gm.fit(samples, (1920, 1080))
        extreme = [1e9] * 9
        self.assertEqual(gm.predict(model, extreme), (2112.0, 1188.0))
        model['coef'] = [[-1e9, -1e9]] + [[0.0, 0.0] for _ in model['coef'][1:]]
        self.assertEqual(gm.predict(model, extreme), (-192.0, -108.0))

    def test_retune_applies_median_translation_without_refitting(self):
        samples = [([x / 1920, y / 1080, 0, 0, 0, 0, 0, 0, 1], (x, y))
                   for x, y in [(100, 100), (960, 100), (1820, 100), (100, 540), (960, 540), (1820, 540), (100, 980), (960, 980), (1820, 980)]]
        model = gm.fit(samples, (1920, 1080))
        shifted = [(f, (gm.predict(model, f)[0] + 600, gm.predict(model, f)[1] - 350)) for f, _ in samples[:5]]
        tuned = gm.retune(model, shifted)
        self.assertEqual(tuned['coef'], model['coef'])
        self.assertAlmostEqual(tuned['offset'][0], 600, delta=1)
        self.assertAlmostEqual(tuned['offset'][1], -350, delta=1)
        self.assertAlmostEqual(gm.predict(tuned, samples[0][0])[0] - gm.predict(model, samples[0][0])[0], 600, delta=1)
        self.assertEqual(model.get('offset'), [0.0, 0.0])
        # Re-tuning again while still drifted by the same amount keeps the correction, not zero.
        again = [(f, (gm.predict(model, f)[0] + 600, gm.predict(model, f)[1] - 350)) for f, _ in samples[:5]]
        self.assertAlmostEqual(gm.retune(tuned, again)['offset'][0], 600, delta=1)
        # A click correction on a re-tuned model adds to the re-tune offset instead of replacing it.
        c = gm.Corrector(tuned)
        for i, (f, _) in enumerate(samples[:6]):
            p = gm.predict(tuned, f)
            c.add(f, (p[0] + 100, p[1]), i)
        self.assertAlmostEqual(c.model['offset'][0], 700, delta=1)

    def test_retune_recovers_drift_that_puts_the_estimate_off_screen(self):
        # A real head move put estimates about 1000 px above the screen. Clamped predictions hid
        # most of that shift, so a re-tune only got back to about 640 px.
        samples = [([x / 1920, y / 1080, 0, 0, 0, 0, 0, 0, 1], (x, y))
                   for x, y in [(100, 100), (960, 100), (1820, 100), (100, 540), (960, 540), (1820, 540), (100, 980), (960, 980), (1820, 980)]]
        good = gm.fit(samples, (1920, 1080))
        drifted = dict(good, offset=[-500.0, -1000.0])
        self.assertEqual(gm.predict(drifted, samples[4][0])[1], -108.0)  # what Ally uses is clamped...
        # ...but the re-tune measures the whole shift: afterwards it predicts like the undrifted model.
        on_target = [(f, gm.predict(good, f)) for f, _ in samples]
        tuned = gm.retune(drifted, on_target[:5])
        for f, (x, y) in on_target:
            px, py = gm.predict(tuned, f)
            self.assertLess(abs(px - x) + abs(py - y), 5)
        c = gm.Corrector(drifted)
        for i, (f, target) in enumerate(on_target[:6]):
            c.add(f, target, i)
        px, py = c.predict(on_target[8][0])
        self.assertLess(abs(px - on_target[8][1][0]) + abs(py - on_target[8][1][1]), 5)

    def _v4_scene(self, pose, true_coef, screen=(1920, 1080)):
        """(features, target) at this head pose, made with the same geometry v4 assumes: the eye
        angles needed to look at each target are produced by the true eye model's inverse."""
        yaw, pitch, tx, ty, tz = pose
        out = []
        for gx in np.linspace(150, 1770, 5):
            for gy in np.linspace(120, 960, 4):
                need = gm._v4_needed([0, 0, 0, 0, yaw, pitch, tx, ty, tz], (gx, gy), screen, gm.DEFAULT_SCREEN_MM)
                h, v = need[0] / true_coef[0], need[1] / true_coef[1]  # linear eyes: angle = k * offset
                out.append(([h, v, 0.0, 0.0, yaw, pitch, tx, ty, tz], (float(gx), float(gy))))
        return out

    def test_v4_keeps_its_aim_when_the_head_moves(self):
        k = (0.9, 1.1)
        calib = self._v4_scene((0.0, -0.10, 0.0, -2.0, -30.0), k)
        v4, v1 = gm.fit(calib, (1920, 1080), 'v4'), gm.fit(calib, (1920, 1080), 'v1')
        self.assertLess(v4['error_px'], 10)
        moved = self._v4_scene((0.14, -0.02, 3.0, -1.0, -42.0), k)  # turned 8 deg, sat back 12 cm, shifted 3 cm
        err = lambda m: float(np.median([np.hypot(*(np.asarray(gm.predict(m, f, clamp=False)) - t)) for f, t in moved]))
        self.assertLess(err(v4), 15)
        self.assertGreater(err(v1), 150)  # the old regression memorised the calibration head pose

    def _lid_scene(self, rel_of_need, v_scale=0.0, pose=(0.0, -0.10, 0.0, -2.0, -30.0)):
        """A v4 scene whose iris barely moves up and down (v_scale), with the lids number appended."""
        out = []
        for (f, t) in self._v4_scene(pose, (0.9, 1.1)):
            need = gm._v4_needed(f, t, (1920, 1080), gm.DEFAULT_SCREEN_MM)
            f = list(f)
            f[1] *= v_scale
            out.append((f + [rel_of_need(need[1])], t))
        return out

    def test_the_lids_carry_up_and_down_when_the_iris_barely_moves(self):
        calib = self._lid_scene(lambda pitch: 0.45 + 1.5 * pitch)
        m = gm.fit(calib, (1920, 1080), 'v4')
        self.assertIsNotNone(m['coef_lid'])
        flat = dict(m, coef_lid=None)
        moved = self._lid_scene(lambda pitch: 0.45 + 1.5 * pitch, pose=(0.1, -0.04, 2.0, -1.0, -38.0))
        dy = lambda model: float(np.median([abs(gm.predict(model, f, clamp=False)[1] - t[1]) for f, t in moved]))
        self.assertLess(dy(m), 15)
        self.assertGreater(dy(flat), 100)  # without the lids, up and down is lost

    def test_lids_that_do_not_help_are_left_out(self):
        rng = np.random.default_rng(3)
        calib = self._lid_scene(lambda pitch: float(rng.uniform(0.2, 0.8)), v_scale=1.0)
        m = gm.fit(calib, (1920, 1080), 'v4')
        self.assertIsNone(m['coef_lid'])
        self.assertLess(m['error_px'], 10)

    def test_older_models_and_unknown_lids_predict_as_before(self):
        calib = self._v4_scene((0.0, -0.10, 0.0, -2.0, -30.0), (0.9, 1.1))
        old = gm.fit(calib, (1920, 1080), 'v4')
        old.pop('coef_lid', None)  # saved before the lids number existed
        f = list(calib[5][0])
        self.assertEqual(gm.predict(old, f + [0.7]), gm.predict(old, f))
        new = gm.fit(self._lid_scene(lambda pitch: 0.45 + 1.5 * pitch, v_scale=1.0), (1920, 1080), 'v4')
        m = dict(new, coef_lid=new['coef_lid'] or [0.0] * 9)
        self.assertEqual(gm.predict(m, f + [float('nan')]), gm.predict(dict(m, coef_lid=None), f))
        g = list(f); g[8] = -0.06  # no head pose: the v1 fallback gets the first nine numbers
        self.assertEqual(gm.predict(m, g + [0.7]), gm.predict(m['fallback'], g))

    def test_v4_falls_back_without_a_head_pose(self):
        calib = self._v4_scene((0.0, -0.10, 0.0, -2.0, -30.0), (0.9, 1.1))
        m = gm.fit(calib, (1920, 1080), 'v4')
        f = list(calib[3][0]); f[8] = -0.06  # no transformation matrix: the rough pose, not centimetres
        self.assertEqual(gm.predict(m, f), gm.predict(m['fallback'], f))
        self.assertTrue(gm.load(self._saved(m), (1920, 1080))['fallback'])

    def _saved(self, model):
        import tempfile
        path = Path(tempfile.mkdtemp()) / 'cal.json'
        gm.save(model, path)
        return path

    def _hybrid_samples(self, screen=(1920, 1080)):
        """Samples whose screen point is exactly the hybrid of v4 eye terms and two CNN angles."""
        rng = np.random.default_rng(1)
        coef = np.zeros((10, 2))
        coef[0, 0], coef[7, 0], coef[9, 0] = 0.35, 0.5, 0.02
        coef[1, 1], coef[8, 1], coef[9, 1] = -0.25, 0.4, -0.01
        shy, shp = gm._V4_SIGNS[2], gm._V4_SIGNS[3]
        samples = []
        while len(samples) < 36:
            h, v, lh, lv = (float(rng.uniform(-0.25, 0.25)) for _ in range(4))
            yaw_deg, pitch_deg = float(rng.uniform(-12, 12)), float(rng.uniform(-8, 8))
            features = [h, v, lh, lv, 0.05, -0.02, 1.0, -1.0, 55.0, yaw_deg, pitch_deg]
            eye = gm.hybrid_terms(features[:9], yaw_deg, pitch_deg) @ coef
            point = gm.project_gaze(features, float(eye[0] + shy * features[4]),
                                    float(eye[1] + shp * features[5]), screen, gm.DEFAULT_SCREEN_MM)
            if point is None or not (80 < point[0] < 1840 and 80 < point[1] < 1000):
                continue
            samples.append((features, point))
        return samples

    def test_v5_recovers_the_hybrid_and_falls_back_without_angles(self):
        screen = (1920, 1080)
        samples = self._hybrid_samples(screen)
        model = gm.fit(samples, screen, 'v5', screen_mm=gm.DEFAULT_SCREEN_MM)
        self.assertEqual(model['features'], 'v5')
        self.assertEqual(model['cnn_model'], 'mobileone_s0')
        self.assertEqual(model['fallback']['features'], 'v4')
        self.assertEqual(len(model['coef']), 10)
        errors = [float(np.hypot(*(np.asarray(gm.predict(model, feat, clamp=False)) - np.asarray(target))))
                  for feat, target in samples]
        self.assertLess(float(np.median(errors)), 5)
        tuned = gm.retune(model, [(feat, (target[0] + 40, target[1] - 15)) for feat, target in samples[:6]])
        self.assertEqual(tuned['features'], 'v5')
        self.assertAlmostEqual(tuned['offset'][0], 40, delta=8)
        corrector = gm.Corrector(model)
        self.assertTrue(corrector.add(samples[0][0], samples[0][1], 0))
        self.assertEqual(len(corrector.predict(samples[1][0])), 2)
        self.assertGreater(gm.radius_px(model, samples[0][0]), 0)
        shown = gm.GazeSmoother().update(gm.predict(model, samples[0][0]), 1.0, 40)
        self.assertEqual(len(shown), 2)
        missing = list(samples[3][0])
        missing[9] = float('nan')
        missing[10] = None
        self.assertEqual(gm.predict(model, missing), gm.predict(model['fallback'], missing[:9]))
        self.assertEqual(gm.predict(model, missing[:9]), gm.predict(model['fallback'], missing[:9]))
        held = gm.validate(model, samples[:12])
        self.assertGreater(held['region_total'], 0)

    def test_features_for_model_adds_cnn_angles_only_for_v5(self):
        lm = self.landmarks(0.1, -0.05, 0.05, 0.08)
        bare = gm.features_for_model({'features': 'v5'}, lm, {}, None, 640, 480)
        self.assertEqual(len(bare[0]), 12)  # v4's nine, the CNN's two, then the lids number
        self.assertTrue(math.isnan(bare[0][9]) and math.isnan(bare[0][10]))
        angled = gm.features_for_model({'features': 'v5'}, lm, {}, None, 640, 480, cnn=(12.5, -3.25))
        self.assertEqual(angled[0][:9], bare[0][:9])
        self.assertEqual(angled[0][9:11], [12.5, -3.25])
        self.assertEqual(angled[0][11], bare[0][11])
        for name in ('v1', 'v2', 'v3', 'v4'):
            without = gm.features_for_model({'features': name}, lm, {}, None, 640, 480)
            with_cnn = gm.features_for_model({'features': name}, lm, {}, None, 640, 480, cnn=(12.5, -3.25))
            self.assertEqual(with_cnn[0], without[0])
            self.assertNotEqual(len(with_cnn[0]), 11)

    def test_v4_is_the_default_and_retunes_like_the_others(self):
        self.assertEqual(gm.DEFAULT_FEATURE_SET, 'v4')
        calib = self._v4_scene((0.0, -0.10, 0.0, -2.0, -30.0), (0.9, 1.1))
        m = gm.fit(calib, (1920, 1080))
        shifted = [(f, (t[0] + 120, t[1] - 80)) for f, t in calib[:5]]
        tuned = gm.retune(m, shifted)
        self.assertAlmostEqual(tuned['offset'][0], 120, delta=5)
        self.assertAlmostEqual(tuned['offset'][1], -80, delta=5)

    def test_fixation(self):
        pts = [(i * 0.05, 100 + i % 2, 100) for i in range(8)] + [(0.4, 500, 500)] + [(0.45 + i * 0.05, 300, 300) for i in range(14)]
        f = gm.fixation(pts, 0, 1.2, 5)
        self.assertIsNotNone(f)
        self.assertGreaterEqual(f['samples'], 5)
        self.assertEqual(f['x'], 300)
        self.assertGreater(f['end'] - f['start'], 0.2)

    def test_fixation_900_points_is_fast(self):
        import time
        points = [(i / 20, 200 + i % 3, 300) for i in range(900)]
        started = time.perf_counter()
        fixation = gm.fixation(points, 0, 45, 5)
        self.assertIsNotNone(fixation)
        self.assertLess(time.perf_counter() - started, 0.05)

    def test_clean_samples_keeps_openness_and_click_frame_is_preceding(self):
        features = [0.1, 0.2, 0, 0]
        retained = gm.clean_samples([(features, (10, 20), 0.7)], preserve_openness=True)
        self.assertEqual(retained, [(features, (10, 20), 0.7)])
        frames = [(9.7, [1]), (9.85, [2]), (9.9, [3]), (10.0, [4]), (10.2, [5])]
        selected = gaze_eval._frame_before_click(frames, 10.0)
        self.assertEqual(selected, frames[2])
        self.assertIsNone(gaze_eval._frame_before_click([(10.01, [6])], 10.0))

    def test_personal_blink_cutoff_uses_target_medians(self):
        samples = [([0], (index // 2, 0), value)
                   for index, value in enumerate((0.10, 0.11, 0.30, 0.32, 0.42, 0.44))]
        cutoff = gm.openness_cutoff(samples)
        self.assertAlmostEqual(cutoff, 0.155)
        self.assertTrue(gm.is_blink({'openness_cutoff': cutoff}, 0.10))
        self.assertFalse(gm.is_blink({'openness_cutoff': cutoff}, 0.20))
        self.assertTrue(gm.is_blink({}, 0.11))

    def test_target_head_movement_checks_pose_and_within_target_spread(self):
        typical = [0.0, 0.0, 0, 0, 1]
        steady = [[0, 0, 0, 0, 0.01, -0.01, 0, 0, 1] for _ in range(10)]
        self.assertFalse(gm.target_head_moved(steady, typical))
        shifted = [[0, 0, 0, 0, 0.07, 0, 0, 0, 1] for _ in range(10)]
        self.assertTrue(gm.target_head_moved(shifted, typical))
        moving = [row[:] for row in steady]
        for row in moving[-4:]:
            row[4] = math.radians(5)
        self.assertTrue(gm.target_head_moved(moving, typical))

    def test_corrector_filter(self):
        samples = []
        for i, (x, y) in enumerate([(100, 100), (960, 100), (1820, 100), (100, 540), (960, 540), (1820, 540), (100, 980), (960, 980), (1820, 980)]):
            samples.append(([x / 1920, y / 1080, 0, 0, 0, 0, 0, 0, 1], (x, y)))
        m = gm.fit(samples, (1920, 1080))
        c = gm.Corrector(m)
        self.assertTrue(c.add(samples[0][0], samples[0][1], 0))
        self.assertTrue(c.add(samples[0][0], (900, 900), 1))
        for i in range(8):
            c.add(samples[i][0], samples[i][1], i + 1)
        self.assertEqual(c.info()['samples_used'], 10)

    def test_corrector_recovers_large_shift_and_preserves_good_model(self):
        targets = [(100, 100), (960, 100), (1820, 100), (100, 540), (960, 540),
                   (1820, 540), (100, 980), (960, 980), (1820, 980)]
        samples = [([x / 1920, y / 1080, 0, 0, 0, 0, 0, 0, 1], (x, y))
                   for x, y in targets]
        model = gm.fit(samples, (1920, 1080))
        shifted = dict(model)
        shifted['coef'] = [row[:] for row in model['coef']]
        shifted['coef'][0][0] += 800
        shifted['coef'][0][1] += 600
        corrector = gm.Corrector(shifted)
        before = np.mean([np.linalg.norm(np.asarray(gm.predict(shifted, f)) - target)
                          for f, target in samples])
        for index in range(10):
            features, target = samples[index % len(samples)]
            self.assertTrue(corrector.add(features, target, index))
        after = np.mean([np.linalg.norm(np.asarray(corrector.predict(f)) - target)
                         for f, target in samples])
        self.assertGreater(before, 750)
        self.assertLess(after, 150)

        good_corrector = gm.Corrector(model)
        good_before = np.mean([np.linalg.norm(np.asarray(gm.predict(model, f)) - target)
                               for f, target in samples])
        for index in range(10):
            features, target = samples[index % len(samples)]
            self.assertTrue(good_corrector.add(features, target, index))
        good_after = np.mean([np.linalg.norm(np.asarray(good_corrector.predict(f)) - target)
                              for f, target in samples])
        self.assertLess(good_after, 150)

    def test_corrector_ignores_scattered_click_errors(self):
        targets = [(100, 100), (960, 100), (1820, 100), (100, 540), (960, 540),
                   (1820, 540), (100, 980), (960, 980), (1820, 980)]
        samples = [([x / 1920, y / 1080, 0, 0, 0, 0, 0, 0, 1], (x, y)) for x, y in targets]
        model = gm.fit(samples, (1920, 1080))
        corrector = gm.Corrector(model)
        original = [corrector.predict(f) for f, _ in samples]
        clicks = [(100, 100), (1820, 980), (960, 540), (100, 980), (1820, 100), (960, 100),
                  (100, 540), (1820, 540), (960, 980), (100, 100), (1820, 980), (960, 540)]
        for index, target in enumerate(clicks):
            corrector.add(samples[index % len(samples)][0], target, index)
        self.assertEqual([corrector.predict(f) for f, _ in samples], original)

    def test_evaluator_tiny_recording(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / 'tiny.jsonl'
            records = [{'rec': 'meta', 'screen': [1920, 1080]}]
            t = 0.0
            phases = [('calibration', [(100, 100), (960, 100), (1820, 100),
                                       (100, 980), (960, 980), (1820, 980)]),
                      ('validation', [(100, 540), (960, 540), (1820, 540)])]
            for phase, points in phases:
                for i, (x, y) in enumerate(points):
                    start = t + 0.1
                    end = start + 0.3
                    records.append({'rec': 'target', 'phase': phase, 'id': i, 'x': x, 'y': y, 'shown': t, 'start': start, 'end': end})
                    gaze_h = x / 1920 - 0.5
                    gaze_v = y / 1080 - 0.5
                    lm = self.landmarks(-gaze_h, gaze_v, gaze_h, gaze_v).tolist()
                    for j in range(7):
                        noisy_lm = np.asarray(lm).copy()
                        noise = (j % 3 - 1) * 0.0005
                        for eye in (gm.RIGHT_EYE, gm.LEFT_EYE):
                            noisy_lm[eye['iris'], 0] += noise
                            noisy_lm[eye['iris'], 1] -= noise
                        records.append({'rec': 'frame', 't': start + j * 0.05, 'w': 640, 'h': 480, 'lm': noisy_lm.tolist(), 'bs': {}, 'mat': None})
                    t = end + 0.1
            p.write_text('\n'.join((json.dumps(r) for r in records)), encoding='utf-8')
            rows = gaze_eval.evaluate(p)
            validation = [row for row in rows if row['phase'] == 'validation']
            self.assertEqual({row['features'] for row in validation}, {'v1', 'v2', 'v3', 'v4'})
            self.assertTrue(all(row['error_px'] < 50 for row in validation), validation)
            for row in validation:
                for method in ('raw', 'one_euro', 'smoother'):
                    self.assertIn(f'{method}_error_median_px', row)
                    self.assertIn(f'{method}_jitter_p90_px', row)
                    self.assertIn(f'{method}_settle_median_ms', row)
            gaze_eval.report([p], Path(td) / 'gaze_eval.csv')
            self.assertTrue((Path(td) / 'gaze_eval.csv').is_file())

    def test_evaluator_includes_v5_only_when_mobileone_angles_exist(self):
        import io
        from contextlib import redirect_stdout

        def write(path, cnn_name=None):
            records = [{'rec': 'meta', 'screen': [1920, 1080], 'screen_mm': [344.0, 193.0]}]
            mat = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 50, 0, 0, 0, 1]
            t = 0.0
            phases = [('calibration', [(100, 100), (960, 100), (1820, 100),
                                       (100, 980), (960, 980), (1820, 980)]),
                      ('validation', [(100, 540), (960, 540), (1820, 540)]),
                      ('drift', [(400, 200), (1500, 800), (960, 400)])]
            for phase, points in phases:
                for i, (x, y) in enumerate(points):
                    start = t + 0.1
                    end = start + 0.3
                    records.append({'rec': 'target', 'phase': phase, 'id': i, 'x': x, 'y': y,
                                    'shown': t, 'start': start, 'end': end})
                    lm = self.landmarks(x / 1920 - 0.5, y / 1080 - 0.5, 0.5 - x / 1920, 0.5 - y / 1080)
                    for j in range(4):
                        row = {'rec': 'frame', 't': start + j * 0.05, 'w': 640, 'h': 480,
                               'lm': lm.tolist(), 'bs': {}, 'mat': mat}
                        if cnn_name:
                            row['cnn'] = {cnn_name: [round((x / 1920 - 0.5) * 20, 4),
                                                     round((y / 1080 - 0.5) * 12, 4)]}
                        records.append(row)
                    t = end + 0.1
            path.write_text('\n'.join(json.dumps(r) for r in records), encoding='utf-8')

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            older, angled, other = root / 'older.jsonl', root / 'angled.jsonl', root / 'resnet.jsonl'
            write(older)
            write(angled, 'mobileone_s0')
            write(other, 'resnet18')
            old_rows = gaze_eval.evaluate(older)
            new_rows = gaze_eval.evaluate(angled)
            other_rows = gaze_eval.evaluate(other)
            self.assertNotIn('v5', {row['features'] for row in old_rows})
            self.assertNotIn('v5', {row['features'] for row in other_rows})
            v5 = [row for row in new_rows if row['features'] == 'v5']
            self.assertEqual({row['phase'] for row in v5}, {'validation', 'drift'})
            for row in v5:
                for method in ('raw', 'one_euro', 'smoother'):
                    self.assertIn(f'{method}_error_median_px', row)
                    self.assertIn(f'{method}_jitter_p90_px', row)
                    self.assertIn(f'{method}_settle_median_ms', row)
            buf = io.StringIO()
            with redirect_stdout(buf):
                gaze_eval.report([older, angled], root / 'gaze_eval.csv')
            text = buf.getvalue()
            self.assertIn('older.jsonl skipped v5', text)
            self.assertIn('no mobileone_s0 angles', text)
            self.assertIn('| angled.jsonl | v5 |', text)
            self.assertNotIn('angled.jsonl skipped v5', text)
if __name__ == '__main__':
    unittest.main()
