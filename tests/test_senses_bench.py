import json
import tempfile
import unittest
from pathlib import Path
from helpers import ROOT
import sys
sys.path.insert(0, str(ROOT))

from ally.senses.bench import read_recording, report, tune
from ally.senses.features import robust_baseline
from ally.senses.detectors import (Baseline, GeometricSquintDetector, HybridSquintDetector,
    RelativeSquintDetector, DistanceLeanDetector, HybridLeanDetector, Tuning)


class BenchTests(unittest.TestCase):
    def _fire(self, det, base, frames):
        out=[]
        for i,f in enumerate(frames):
            signal=det.feed(f,i/10,base)
            if signal:out.append(signal)
        return out

    def test_relative_detector_scales_with_personal_baseline(self):
        for rest in (.25,.45):
            base={"eyeSquint":{"median":rest,"mad":.02},"aperture":{"median":.3}}
            frames=[{"eyeSquint":rest+.2,"eyeSquintLeft":rest+.2,"eyeSquintRight":rest+.2,
                     "head_yaw":0,"head_pitch":0,"eyeBlink":0,"mouthSmile":0}]*40
            self.assertTrue(self._fire(RelativeSquintDetector(Tuning()),base,frames))

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
        dbase={"head_distance":{"median":40},"face_width":{"median":.3}}
        close={"head_distance":52,"width":.3,"yaw":0,"pitch":0}
        self.assertTrue(self._fire(DistanceLeanDetector(Tuning()),dbase,[close]*40))
        self.assertFalse(self._fire(HybridLeanDetector(Tuning()),base,[{**width,"yaw":30}]*40))

    def test_read_both_recording_shapes(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/"small.jsonl"
            rows=[{"rec":"meta","alias":"A","eyes":"hooded"},
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
            report([str(rec)],out)
            self.assertTrue((out/"report.md").exists())
            self.assertTrue((out/"report.csv").exists())
            self.assertEqual(sorted(p.name for p in td.iterdir()),["out","r.jsonl"])

    def test_tuning_requires_three_people(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/"r.jsonl"
            p.write_text(json.dumps({"rec":"meta","alias":"one"}),encoding="utf-8")
            with self.assertRaisesRegex(SystemExit,"fewer than 3 people"):
                tune([str(p)])


if __name__=="__main__":unittest.main()
