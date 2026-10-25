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
        self.assertAlmostEqual(hit[0], 982.8837, delta=0.1)
        self.assertAlmostEqual(hit[1], 11.4715, delta=0.1)

    def test_v3_geometric_gaze_is_stable_across_head_pose(self):
        target = np.array([0.08, 0.09])
        vectors = []
        v1 = []
        for yaw, tx, tz in ((0, 0, 40), (math.radians(12), 4, 45)):
            c, s = math.cos(yaw), math.sin(yaw)
            rot = np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])
            trans = np.array([tx, 0, tz])
            lm = self.landmarks()
            for side, ids in ((-1, gm.RIGHT_EYE), (1, gm.LEFT_EYE)):
                eye = trans + rot @ np.array([side * 3.2, 0, 0])
                desired = np.array([(target[0] - .5) * 344, target[1] * 193, -1]) - eye
                local = rot.T @ desired
                h, v = local[0] / -local[2] / 2.5, local[1] / -local[2] / 2.5
                lm[ids['iris'], 0] = .5 + h * .2
                lm[ids['iris'], 1] = .5 + v * .2
            mat = np.eye(4)
            mat[:3, :3], mat[:3, 3] = rot, trans
            v3 = gm.features_for_model({'features': 'v3'}, lm, {}, mat, 640, 480)[0]
            v1.append(gm.features(lm, {}, mat, 640, 480)[0])
            vectors.append(v3[:2])
        self.assertLess(np.linalg.norm(np.asarray(vectors[0]) - vectors[1]), .02)
        self.assertGreater(np.linalg.norm(np.asarray(v1[0][:2]) - v1[1][:2]), .02)

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
            self.assertEqual({row['features'] for row in validation}, {'v1', 'v2', 'v3'})
            self.assertTrue(all(row['error_px'] < 50 for row in validation), validation)
            gaze_eval.report([p], Path(td) / 'gaze_eval.csv')
            self.assertTrue((Path(td) / 'gaze_eval.csv').is_file())
if __name__ == '__main__':
    unittest.main()
