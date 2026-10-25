import json
import tempfile
import unittest
from pathlib import Path
from helpers import ROOT
import sys
sys.path.insert(0, str(ROOT))

from ally.senses.bench import read_recording, report, tune
from ally.senses.features import extract, robust_baseline
from ally.senses.detectors import (Baseline, GeometricSquintDetector, HybridSquintDetector,
    HybridPersonalSquintDetector, PersonalSquintDetector, RelativeSquintDetector,
    DistanceLeanDetector, HybridLeanDetector, Tuning)
from ally.senses import bench
from ally.senses import face


class BenchTests(unittest.TestCase):
    def _fire(self, det, base, frames):
        out=[]
        for i,f in enumerate(frames):
            signal=det.feed(f,i/10,base)
            if signal:out.append(signal)
        return out

    def test_relative_detector_scales_with_personal_baseline(self):
        for rest, aperture in ((.25,.30),(.45,.18)):
            base={"eyeSquint":{"median":rest,"mad":.02},"aperture":{"median":aperture}}
            frames=[{"eyeSquint":rest+.2,"eyeSquintLeft":rest+.2,"eyeSquintRight":rest+.2,
                     "head_yaw":0,"head_pitch":0,"eyeBlink":0,"mouthSmile":0}]*40
            self.assertTrue(self._fire(RelativeSquintDetector(Tuning()),base,frames))
        down=[{"eyeSquint":.55,"eyeSquintLeft":.55,"eyeSquintRight":.55,"head_yaw":0,"head_pitch":0,
               "eyeBlink":0,"mouthSmile":0,"eyeLookDown":.9}]*40
        self.assertFalse(self._fire(RelativeSquintDetector(Tuning()),
                                    {"eyeSquint":{"median":.25,"mad":.02}},down))

    def test_all_raw_squint_candidates_reject_common_non_squint_frames(self):
        base={"eyeSquint":{"median":.25,"mad":.02},"aperture":{"median":.3},
              "aperture_l":{"median":.3},"aperture_r":{"median":.3},
              "lower_height":{"median":.1},"lower_height_l":{"median":.1},
              "lower_height_r":{"median":.1},"upper_height":{"median":.1}}
        positive={"eyeSquint":.55,"eyeSquintLeft":.55,"eyeSquintRight":.55,
            "aperture":.2,"aperture_l":.2,"aperture_r":.2,"lower_height":.15,
            "lower_height_l":.15,"lower_height_r":.15,"upper_height":.1,"iris_vertical":.5,
            "eyeBlink":0,"mouthSmile":0,"cheekSquint":0,"jawOpen":0,"eyeLookDown":0,
            "head_yaw":0,"head_pitch":0}
        negatives=(
            {"eyeLookDown":.9,"iris_vertical":.9,"upper_height":.05,"lower_height":.1},
            {"eyeBlink":.95,"aperture":0,"aperture_l":0,"aperture_r":0},
            {"mouthSmile":.8,"cheekSquint":.8},
            {"jawOpen":.9,"mouthSmile":.8,"cheekSquint":.8},
            {"aperture_l":.1,"lower_height_l":.2,"aperture_r":.3,"lower_height_r":.1,
             "eyeSquintLeft":.8,"eyeSquintRight":.25,"eyeSquint":.525},
            {"head_yaw":30},
        )
        for cls in (RelativeSquintDetector,GeometricSquintDetector,HybridSquintDetector):
            for changes in negatives:
                with self.subTest(detector=cls.__name__, negative=changes):
                    self.assertFalse(self._fire(cls(Tuning()),base,[{**positive,**changes}]*40))

    def test_geometric_requires_bilateral_aperture_and_lower_lid(self):
        base={"aperture_l":{"median":.3},"aperture_r":{"median":.3},
              "lower_height_l":{"median":.1},"lower_height_r":{"median":.1},
              "upper_height":{"median":.1}}
        pos={"aperture_l":.2,"aperture_r":.2,"lower_height_l":.15,"lower_height_r":.15,
             "upper_height":.1,"iris_vertical":.5,"eyeBlink":0,"mouthSmile":0,"cheekSquint":0,
             "jawOpen":0,"head_yaw":0,"head_pitch":0,"eyeSquint":.6}
        self.assertTrue(self._fire(GeometricSquintDetector(Tuning()),base,[pos]*40))
        for changes in ({"iris_vertical":.9,"upper_height":.05},{"eyeBlink":.9},
                        {"mouthSmile":.8,"cheekSquint":.8},{"jawOpen":.9},
                        {"aperture_l":.1,"lower_height_l":.2,"aperture_r":.3,"lower_height_r":.1},
                        {"head_yaw":30}):
            self.assertFalse(self._fire(GeometricSquintDetector(Tuning()),base,[{**pos,**changes}]*40))

    def test_hybrid_detector_uses_geometry_and_relative_evidence(self):
        base={"eyeSquint":{"median":.25,"mad":.02},"aperture_l":{"median":.3},"aperture_r":{"median":.3},
              "lower_height_l":{"median":.1},"lower_height_r":{"median":.1},"upper_height":{"median":.1}}
        pos={"eyeSquint":.55,"aperture_l":.2,"aperture_r":.2,"lower_height_l":.15,"lower_height_r":.15,
             "upper_height":.1,"iris_vertical":.5,"eyeBlink":0,"mouthSmile":0,"cheekSquint":0,
             "jawOpen":0,"head_yaw":0,"head_pitch":0}
        self.assertTrue(self._fire(HybridSquintDetector(Tuning()),base,[pos]*40))

    def test_lean_distance_and_width_candidates_gate_turns(self):
        base=Baseline(.1,.3)
        width={"width":.39,"yaw":0,"pitch":0}
        self.assertTrue(self._fire(HybridLeanDetector(Tuning()),base,[width]*40))
        dbase={"head_distance":{"median":52},"face_width":{"median":.3}}
        close={"head_distance":40,"width":.3,"yaw":0,"pitch":0}
        self.assertTrue(self._fire(DistanceLeanDetector(Tuning()),dbase,[close]*40))
        self.assertFalse(self._fire(HybridLeanDetector(Tuning()),base,[{**width,"yaw":30}]*40))

    def test_read_both_recording_shapes(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/"small.jsonl"
            rows=[{"rec":"meta","alias":"A","eyes":"hooded"},
                  {"rec":"helper","ready":True},
                  {"rec":"helper","t":1,"f":{"squint_l":.2}},
                  {"rec":"label","name":"squint","start":1,"end":2,"expect":"squint"}]
            p.write_text("\n".join(map(json.dumps,rows)),encoding="utf-8")
            got=read_recording(p)
            self.assertEqual(got["meta"]["alias"],"A")
            self.assertEqual(got["segments"][0]["expect"],"squint")
            q=Path(td)/"gaze.jsonl"
            q.write_text("\n".join(map(json.dumps,[{"rec":"meta","alias":"B"},
                {"rec":"frame","t":1,"w":640,"h":480,"lm":[],"bs":{},"mat":[0]*16},
                {"rec":"target","phase":"calibration","pose":"close","start":1,"end":2}])) ,encoding="utf-8")
            g=read_recording(q)
            self.assertEqual(g["segments"][0]["expect"],"lean")
            self.assertIn("bs",g["frames"][0]["raw"])

    def test_robust_baseline_median_mad_room(self):
        b=robust_baseline([{"eyeSquint":.25},{"eyeSquint":.27},{"eyeSquint":.9}])
        self.assertAlmostEqual(b["eyeSquint"]["median"],.27)
        self.assertAlmostEqual(b["eyeSquint"]["room"],.73)

    def test_report_writes_only_requested_output(self):
        with tempfile.TemporaryDirectory() as td:
            td=Path(td); rec=td/"r.jsonl"; out=td/"out"
            rec.write_text("\n".join(map(json.dumps,[{"rec":"meta","alias":"P"},
              {"rec":"helper","t":1,"f":{"squint_l":.1,"squint_r":.1,"width":.3,"blink":0,"smile":0,"yaw":0,"pitch":0}},
              {"rec":"label","name":"reading","start":1,"end":1,"expect":"natural"}])) ,encoding="utf-8")
            rows = report([str(rec)],out)
            self.assertTrue((out/"report.md").exists())
            self.assertTrue((out/"report.csv").exists())
            self.assertEqual(sorted(p.name for p in td.iterdir()),["out","r.jsonl"])
            lean_hybrid = [row for row in rows
                           if row["kind"] == "lean" and row["method"] == "hybrid"]
            self.assertTrue(lean_hybrid)
            self.assertTrue(all(row["squint_off"] == "" for row in lean_hybrid))

    def test_tuning_requires_three_people(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/"r.jsonl"
            p.write_text(json.dumps({"rec":"meta","alias":"one"}),encoding="utf-8")
            with self.assertRaisesRegex(SystemExit,"fewer than 3 people"):
                tune([str(p)])

    def test_relative_squint_is_person_scaled_across_eye_shapes_and_resting_scores(self):
        for rest in (0.15, 0.35, 0.55):
            change = (1 - rest) * 0.2
            baseline = {"eyeSquint": {"median": rest, "mad": 0.02}}
            frames = [{"eyeSquint": rest + change,
                       "eyeSquintLeft": rest + change,
                       "eyeSquintRight": rest + change,
                       "head_yaw": 0,
                       "head_pitch": 0,
                       "eyeBlink": 0,
                       "mouthSmile": 0} for _ in range(40)]
            detector = RelativeSquintDetector(Tuning())
            self.assertTrue(self._fire(detector, baseline, frames))

    def test_personal_model_fires_for_bilateral_squint_and_rejects_confounders(self):
        parts = _personal_parts()
        model = PersonalSquintDetector.fit(parts)
        self.assertTrue(model["enabled"], model["why"])
        baseline = {"personal_model": model}
        positive = _personal_frame(aperture=0.30, lower_raise=0.16)
        detector = PersonalSquintDetector(Tuning())
        self.assertTrue(self._fire(detector, baseline, [positive] * 40))

        confounders = [
            _personal_frame(aperture=0.34, lower_raise=0.105,
                            iris_vertical=0.75, eyeLookDown=0.8, head_pitch=5),
            _personal_frame(aperture=0.35, lower_raise=0.12, mouthSmile=0.6),
            _personal_frame(aperture=0.30, lower_raise=0.16, eyeBlink=0.9),
            _personal_frame(aperture=0.30, lower_raise=0.16, head_yaw=30),
        ]
        for frame in confounders:
            detector = PersonalSquintDetector(Tuning())
            self.assertFalse(self._fire(detector, baseline, [frame] * 40))

        wink = _personal_frame(aperture=0.4, lower_raise=0.1)
        wink.update(aperture_r=0.30, lower_raise_r=0.16)
        detector = PersonalSquintDetector(Tuning())
        self.assertFalse(self._fire(detector, baseline, [wink] * 40))

    def test_personal_model_abstains_when_deliberate_squint_is_not_separable(self):
        parts = _personal_parts()
        parts["squint"] = [dict(sample) for sample in parts["neutral"]]
        model = PersonalSquintDetector.fit(parts)
        self.assertFalse(model["enabled"])
        self.assertIn("not distinct", model["why"])

    def test_hybrid_uses_person_relative_votes_and_au12_smile_explanation(self):
        model = PersonalSquintDetector.fit(_personal_parts())
        model["threshold"] = 3.0
        model["deliberate_score"] = 10.0
        baseline = {
            "personal_model": model,
            "eyeSquint": {"median": 0.2, "mad": 0.02},
            "au6": {"median": 0.1, "mad": 0.02},
            "au7": {"median": 0.1, "mad": 0.02},
            "au12": {"median": 0.1, "mad": 0.02},
            "mouthSmile": {"median": 0.0, "mad": 0.02},
        }
        frame = _personal_frame(aperture=0.33, lower_raise=0.1)
        frame["au"] = [0.0, 0.0, 0.0, 0.1, 0.1, 0.0, 0.1,
                       0.0, 0.0, 0.0, 0.0, 0.0]
        detector = HybridPersonalSquintDetector(Tuning())
        self.assertFalse(self._fire(detector, baseline, [frame] * 40))

        au7_frame = dict(frame)
        au7_frame["au"] = list(frame["au"])
        au7_frame["au"][4] = 0.3
        detector = HybridPersonalSquintDetector(Tuning())
        self.assertTrue(self._fire(detector, baseline, [au7_frame] * 40))

        smiling_frame = dict(au7_frame)
        smiling_frame["au"] = list(au7_frame["au"])
        smiling_frame["au"][6] = 0.4
        detector = HybridPersonalSquintDetector(Tuning())
        self.assertFalse(self._fire(detector, baseline, [smiling_frame] * 40))

        relative_frame = dict(frame)
        relative_frame["eyeSquint"] = 0.3
        detector = HybridPersonalSquintDetector(Tuning())
        self.assertTrue(self._fire(detector, baseline, [relative_frame] * 40))

    def test_feature_geometry_uses_pixels_and_research_landmarks(self):
        landmarks = [[0.0, 0.0, 0.0] for _point in range(478)]
        _set_eye(landmarks, 33, 133, (160, 159, 158), (144, 145, 153),
                 (469, 470, 471, 472), 468, (46, 53, 52, 65, 55), 100)
        _set_eye(landmarks, 263, 362, (387, 386, 385), (373, 374, 380),
                 (474, 475, 476, 477), 473, (276, 283, 282, 295, 285), 200)
        matrix = [1, 0, 0, 2, 0, 1, 0, 3, 0, 0, 1, 4, 0, 0, 0, 1]
        values = extract(landmarks, {}, matrix, 640, 480)
        self.assertAlmostEqual(values["iris_diameter"], 11.7, places=5)
        self.assertAlmostEqual(values["aperture"], 8 / 11.7, places=5)
        self.assertAlmostEqual(values["lower_raise"], 4 / 11.7, places=5)
        self.assertAlmostEqual(values["brow_distance_r"], 16 / 11.7, places=5)
        self.assertAlmostEqual(values["head_distance"], (29 ** 0.5), places=5)
        self.assertEqual((values["head_yaw"], values["head_pitch"]), face.head_angles(matrix))

    def test_benchmark_training_split_excludes_all_fit_parts(self):
        segments = [
            _segment("calibration", "baseline"),
            _segment("look_top", "none"),
            _segment("look_bottom", "none"),
            _segment("smile", "none"),
            _segment("act_squint", "squint"),
            _segment("read_normal", "none"),
            _segment("squint_soft", "squint"),
            _segment("act_squint_repeat", "squint"),
        ]
        training, used = bench._training_segments(segments)
        self.assertEqual(used, {0, 1, 2, 3, 4})
        self.assertEqual(len(training["squint"]), 1)
        labels = bench._labels_for({"segments": segments, "frames": []}, used)
        scored_names = [segment["name"] for segment, _label, _positive in labels]
        self.assertEqual(scored_names, ["read_normal", "squint_soft", "act_squint_repeat"])

    def test_london_still_reader_pairs_neutral_and_smiling_photos(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "london.jsonl"
            raw = {"lm": [], "bs": {}, "mat": [0] * 16, "w": 10, "h": 10}
            rows = [
                {"rec": "still", "person": "p1", "cond": "neutral", **raw},
                {"rec": "still", "person": "p1", "cond": "smiling", **raw},
            ]
            path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
            self.assertEqual(set(bench.read_london(path)["p1"]), {"neutral", "smiling"})

    def test_v1_raw_replay_uses_live_face_signals(self):
        landmarks = [[0.5, 0.5, 0.0] for _point in range(478)]
        blendshapes = {"eyeSquintLeft": 0.22, "eyeSquintRight": 0.24}
        matrix = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 50, 0, 0, 0, 1]
        raw = {"lm": landmarks, "bs": blendshapes, "mat": matrix, "w": 640, "h": 480}
        path_row = {"f": {}, "raw": raw}
        self.assertEqual(bench._legacy_signals(path_row),
                         face.signals(landmarks, blendshapes, matrix, 640, 480))


def _segment(name, expectation):
    return {"name": name, "expect": expectation, "phase": "", "pose": "normal",
            "start": 0.0, "end": 4.0}


def _personal_parts():
    neutral = []
    for index in range(80):
        noise = (index % 7 - 3) * 0.001
        neutral.append(_personal_frame(aperture=0.4 + noise,
                                       lower_raise=0.1 + noise / 2))
    top = [_personal_frame(aperture=0.41, lower_raise=0.1,
                           iris_vertical=0.45, head_pitch=-3) for _index in range(30)]
    bottom = [_personal_frame(aperture=0.34, lower_raise=0.105,
                              iris_vertical=0.75, eyeLookDown=0.8,
                              head_pitch=5) for _index in range(30)]
    smile = [_personal_frame(aperture=0.35, lower_raise=0.12,
                             mouthSmile=0.6) for _index in range(30)]
    squint = [_personal_frame(aperture=0.30, lower_raise=0.16) for _index in range(30)]
    return {"neutral": neutral, "top": top, "bottom": bottom,
            "smile": smile, "squint": squint}


def _personal_frame(aperture, lower_raise, iris_vertical=0.5, eyeLookDown=0.0,
                    mouthSmile=0.0, eyeBlink=0.0, head_yaw=0.0, head_pitch=0.0):
    return {
        "aperture": aperture,
        "aperture_r": aperture,
        "aperture_l": aperture,
        "lower_raise": lower_raise,
        "lower_raise_r": lower_raise,
        "lower_raise_l": lower_raise,
        "iris_vertical": iris_vertical,
        "eyeLookDown": eyeLookDown,
        "mouthSmile": mouthSmile,
        "eyeSquint": 0.2 + mouthSmile * 0.1,
        "eyeSquintLeft": 0.2 + mouthSmile * 0.1,
        "eyeSquintRight": 0.2 + mouthSmile * 0.1,
        "eyeBlink": eyeBlink,
        "head_yaw": head_yaw,
        "head_pitch": head_pitch,
        "head_distance": 50.0,
        "iris_diameter": 12.0,
        "face_width": 180.0,
    }


def _set_eye(landmarks, outer, inner, uppers, lowers, iris_ring, iris_center,
             brows, left_x):
    center_x = (left_x + 20) / 640
    center_y = 100 / 480
    landmarks[outer] = [left_x / 640, center_y, 0.0]
    landmarks[inner] = [(left_x + 40) / 640, center_y, 0.0]
    for index in uppers:
        landmarks[index] = [(left_x + 20) / 640, 96 / 480, 0.0]
    for index in lowers:
        landmarks[index] = [(left_x + 20) / 640, 104 / 480, 0.0]
    landmarks[iris_center] = [center_x, center_y, 0.0]
    radius = 5.85
    offsets = ((radius, 0), (0, radius), (-radius, 0), (0, -radius))
    for index, (offset_x, offset_y) in zip(iris_ring, offsets):
        landmarks[index] = [(left_x + 20 + offset_x) / 640,
                            (100 + offset_y) / 480, 0.0]
    for index in brows:
        landmarks[index] = [(left_x + 20) / 640, 80 / 480, 0.0]


if __name__=="__main__":unittest.main()
