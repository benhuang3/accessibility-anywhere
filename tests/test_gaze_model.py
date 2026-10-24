import json, tempfile, unittest
from pathlib import Path
import helpers  # adds the repository root to sys.path
import numpy as np
from ally import gaze_model as gm
from ally import gaze_eval

class GazeModelTests(unittest.TestCase):
    def landmarks(self, rh=0, rv=0, lh=0, lv=0):
        a=np.zeros((478,3),float)
        for ids,h,v in ((gm.RIGHT_EYE,rh,rv),(gm.LEFT_EYE,lh,lv)):
            a[ids['inner'],:2]=[.4,.5]; a[ids['outer'],:2]=[.6,.5]
            a[ids['top'],:2]=[.5,.48]; a[ids['bottom'],:2]=[.5,.52]
            a[ids['iris'],:2]=[.5+h*.2,.5+v*.2]
        return a

    def test_per_eye_feature_directions(self):
        zero=gm.features_for_model({'features':'v2'},self.landmarks(),{},None,640,480)[0]
        moved=gm.features_for_model({'features':'v2'},self.landmarks(.1,.1,.1,.1),{'eyeLookUpLeft':.7,'eyeLookUpRight':.6},None,640,480)[0]
        self.assertGreater(moved[0],zero[0]); self.assertGreater(moved[2],zero[2])
        self.assertGreater(moved[1],zero[1]); self.assertGreater(moved[3],zero[3]); self.assertGreater(moved[6],0)

    def test_v2_lid_feature_names_match_vector(self):
        blendshapes = {"eyeBlinkLeft": .2, "eyeBlinkRight": .6}
        landmarks = self.landmarks()
        landmarks[gm.LEFT_EYE["top"], 1] = .47
        landmarks[gm.LEFT_EYE["bottom"], 1] = .53
        vector = gm.features_for_model({"features": "v2"}, landmarks, blendshapes, None, 640, 480)[0]
        values = dict(zip(gm.FEATURES_V2, vector))
        self.assertAlmostEqual(values["left_lid"], .225)
        self.assertAlmostEqual(values["right_lid"], .15)

    def test_fit_validate_region_and_radius(self):
        samples=[]
        for x,y in [(100,100),(960,100),(1820,100),(100,540),(960,540),(1820,540),(100,980),(960,980),(1820,980)]:
            f=[x/1920,y/1080,0,0,0,0,0,0,1]; samples.append((f,(x,y)))
        m=gm.fit(samples,(1920,1080)); self.assertLess(gm.validate(m,samples)['error_px'],40)
        self.assertEqual(gm.region(10,10,(1920,1080)),"top left"); self.assertEqual(gm.region(960,540,(1920,1080)),"middle")
        self.assertGreater(gm.radius_px(m,[0,0,0,0,1,1,0,0,0]),gm.radius_px(m,[0]*9))

    def test_fixation(self):
        pts=[(i*.05,100+(i%2),100) for i in range(8)]+[(.4,500,500)]+[(.45+i*.05,300,300) for i in range(8)]
        f=gm.fixation(pts,0,1,5); self.assertIsNotNone(f); self.assertEqual(f['x'],100.5); self.assertGreaterEqual(f['samples'],5)

    def test_corrector_filter(self):
        samples=[]
        for i,(x,y) in enumerate([(100,100),(960,100),(1820,100),(100,540),(960,540),(1820,540),(100,980),(960,980),(1820,980)]):
            samples.append(([x/1920,y/1080,0,0,0,0,0,0,1],(x,y)))
        m=gm.fit(samples,(1920,1080)); c=gm.Corrector(m)
        self.assertTrue(c.add(samples[0][0],samples[0][1],0)); self.assertFalse(c.add(samples[0][0],(900,900),1))
        for i in range(8): c.add(samples[i][0],samples[i][1],i+1)
        self.assertEqual(c.info()['samples_used'],9)

    def test_evaluator_tiny_recording(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'tiny.jsonl'; records=[{'rec':'meta','screen':[1920,1080]}]
            # Simple valid landmarks and enough distinct calibration targets.
            lm=self.landmarks().tolist(); t=0.
            for phase,points in [('calibration',[(100,100),(960,100),(1820,100),(100,540),(960,540),(1820,540)]),('validation',[(100,980),(960,980),(1820,980)])]:
                for i,(x,y) in enumerate(points):
                    start=t+.1; end=start+.3
                    records.append({'rec':'target','phase':phase,'id':i,'x':x,'y':y,'shown':t,'start':start,'end':end})
                    for j in range(7): records.append({'rec':'frame','t':start+j*.05,'w':640,'h':480,'lm':lm,'bs':{},'mat':None})
                    t=end+.1
            p.write_text('\n'.join(json.dumps(r) for r in records),encoding='utf-8')
            rows=gaze_eval.evaluate(p); self.assertTrue(rows); self.assertEqual({r['phase'] for r in rows},{'validation','drift'})

if __name__=='__main__': unittest.main()
