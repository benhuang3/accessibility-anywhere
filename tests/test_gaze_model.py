import json
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

    def test_corrector_filter(self):
        samples = []
        for i, (x, y) in enumerate([(100, 100), (960, 100), (1820, 100), (100, 540), (960, 540), (1820, 540), (100, 980), (960, 980), (1820, 980)]):
            samples.append(([x / 1920, y / 1080, 0, 0, 0, 0, 0, 0, 1], (x, y)))
        m = gm.fit(samples, (1920, 1080))
        c = gm.Corrector(m)
        self.assertTrue(c.add(samples[0][0], samples[0][1], 0))
        self.assertFalse(c.add(samples[0][0], (900, 900), 1))
        for i in range(8):
            c.add(samples[i][0], samples[i][1], i + 1)
        self.assertEqual(c.info()['samples_used'], 9)

    def test_corrector_refines_shifted_model_and_preserves_good_model(self):
        targets = [(100, 100), (960, 100), (1820, 100), (100, 540), (960, 540),
                   (1820, 540), (100, 980), (960, 980), (1820, 980)]
        samples = [([x / 1920, y / 1080, 0, 0, 0, 0, 0, 0, 1], (x, y))
                   for x, y in targets]
        model = gm.fit(samples, (1920, 1080))
        shifted = dict(model)
        shifted['coef'] = [row[:] for row in model['coef']]
        shifted['coef'][0][0] += 150
        shifted['coef'][0][1] += 150
        corrector = gm.Corrector(shifted)
        before = np.mean([np.linalg.norm(np.asarray(gm.predict(shifted, f)) - target)
                          for f, target in samples])
        for index in range(10):
            features, target = samples[index % len(samples)]
            self.assertTrue(corrector.add(features, target, index))
        after = np.mean([np.linalg.norm(np.asarray(corrector.predict(f)) - target)
                         for f, target in samples])
        self.assertGreater(before - after, 100)
        prediction = corrector.predict(samples[0][0])
        self.assertFalse(corrector.add(samples[0][0], (prediction[0] + 300, prediction[1]), 11))

        good_corrector = gm.Corrector(model)
        good_before = np.mean([np.linalg.norm(np.asarray(gm.predict(model, f)) - target)
                               for f, target in samples])
        for index in range(10):
            features, target = samples[index % len(samples)]
            self.assertTrue(good_corrector.add(features, target, index))
        good_after = np.mean([np.linalg.norm(np.asarray(good_corrector.predict(f)) - target)
                              for f, target in samples])
        self.assertLessEqual(good_after, good_before)

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
            self.assertEqual({row['features'] for row in validation}, {'v1', 'v2'})
            self.assertTrue(all(row['error_px'] < 50 for row in validation), validation)
            gaze_eval.report([p])
            self.assertTrue((Path(td) / 'gaze_eval.csv').is_file())
if __name__ == '__main__':
    unittest.main()
