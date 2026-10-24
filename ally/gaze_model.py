"""Pure numpy webcam gaze features and calibration helpers."""
from __future__ import annotations

import json, math, time
from collections import deque
from pathlib import Path
import numpy as np

RIGHT_EYE = {"outer": 33, "inner": 133, "top": 159, "bottom": 145, "iris": 468}
LEFT_EYE = {"outer": 263, "inner": 362, "top": 386, "bottom": 374, "iris": 473}
FEATURES = ["h", "v", "look_h", "look_v", "yaw", "pitch", "tx", "ty", "tz"]
FEATURES_V2 = ["right_h", "right_v", "left_h", "left_v", "left_lid", "right_lid",
               "up_left", "down_left", "up_right", "down_right", "yaw", "pitch", "roll",
               "tx", "ty", "tz"]
MODEL_VERSION = 1
DEFAULT_FEATURE_SET = "v1"

def _eye(pts, idx):
    inner, outer = pts[idx["inner"], :2], pts[idx["outer"], :2]
    axis = outer-inner; width = float(np.hypot(*axis)) or 1e-6
    ux = axis/width; uy = np.array([-ux[1], ux[0]])
    if uy[1] < 0: uy = -uy
    rel = pts[idx["iris"], :2]-(inner+outer)/2
    return float(rel@ux)/width, float(rel@uy)/width, float(np.hypot(*(pts[idx["top"],:2]-pts[idx["bottom"],:2])))/width

def _frame_parts(landmarks, blendshapes, matrix, width, height):
    pts=np.asarray(landmarks,dtype=float)
    if pts.shape[0]<478: return None
    pts=pts*np.array([width,height,width]); rh,rv,ro=_eye(pts,RIGHT_EYE); lh,lv,lo=_eye(pts,LEFT_EYE); b=blendshapes or {}
    if matrix is not None:
        m=np.asarray(matrix,dtype=float).reshape(4,4); rot=m[:3,:3]
        fwd=rot@np.array([0.,0.,1.]); yaw=math.atan2(fwd[0],fwd[2]); pitch=math.atan2(fwd[1],fwd[2])
        roll=math.atan2(rot[1,0],rot[0,0]); tx,ty,tz=map(float,m[:3,3])
    else:
        yaw=float(pts[1,0]/width-.5); pitch=float(pts[1,1]/height-.5); roll=0.
        tx,ty,tz=yaw,pitch,float(np.hypot(*(pts[263,:2]-pts[33,:2])))/width
    blink=max(b.get("eyeBlinkLeft",0),b.get("eyeBlinkRight",0)); openness=min(lo,ro)*(1-blink)
    v1=[(lh-rh)/2,(lv+rv)/2,(b.get("eyeLookInLeft",0)+b.get("eyeLookOutRight",0)-b.get("eyeLookOutLeft",0)-b.get("eyeLookInRight",0))/2,
        (b.get("eyeLookUpLeft",0)+b.get("eyeLookUpRight",0)-b.get("eyeLookDownLeft",0)-b.get("eyeLookDownRight",0))/2,yaw,pitch,tx,ty,tz]
    v2=[rh,rv,lh,lv,lo,ro,b.get("eyeLookUpLeft",0),b.get("eyeLookDownLeft",0),b.get("eyeLookUpRight",0),b.get("eyeLookDownRight",0),yaw,pitch,roll,tx,ty,tz]
    return v1,v2,openness,(yaw,pitch,roll,tx,ty,tz)

def features(landmarks, blendshapes: dict|None, matrix, width:int, height:int):
    p=_frame_parts(landmarks,blendshapes,matrix,width,height)
    return None if p is None else (p[0],p[2])

def features_for_model(model, landmarks, blendshapes, matrix, width, height):
    p=_frame_parts(landmarks,blendshapes,matrix,width,height)
    return None if p is None else (p[1] if model.get("features","v1")=="v2" else p[0],p[2])

def expand(f, feature_set="v1"):
    f=np.atleast_2d(np.asarray(f,dtype=float))
    if feature_set=="v1":
        h,v,lh,lv=f[:,:4].T
        return np.column_stack([f,h*h,v*v,h*v,lh*lh,lv*lv,lh*lv])
    # V2 includes iris-by-head interactions to model pose-dependent gaze shifts.
    h1,v1,h2,v2=f[:,:4].T; head=f[:,10:16]
    return np.column_stack([f,h1*head[:,0],v1*head[:,1],h2*head[:,0],v2*head[:,1],h1*v1,h2*v2])

def _ridge(x,y,lam): return np.linalg.solve(x.T@x+lam*np.diag([0.]+[1.]*(x.shape[1]-1)),x.T@y)

def fit(samples, screen, feature_set=DEFAULT_FEATURE_SET):
    if feature_set not in ("v1","v2"): raise ValueError("feature_set must be v1 or v2")
    targets=sorted({tuple(t) for _f,t in samples})
    if len(targets)<6: raise ValueError(f"saw your eyes at only {len(targets)} of the dots")
    raw=expand([f for f,_ in samples],feature_set); y=np.array([t for _,t in samples],float)
    mean,std=raw.mean(0),raw.std(0); std[std<1e-9]=1
    x=np.column_stack([np.ones(len(raw)),(raw-mean)/std]); groups=np.array([targets.index(tuple(t)) for _,t in samples])
    def err(lam):
        es=[]
        for g in range(len(targets)):
            tr,te=groups!=g,groups==g; pred=np.median(x[te]@_ridge(x[tr],y[tr],lam),axis=0)
            es.append(float(np.hypot(*(pred-y[te][0]))))
        return float(np.median(es))
    lam,e=min(((l,err(l)) for l in (.1,1.,10.,100.)),key=lambda z:z[1]); coef=_ridge(x,y,lam)
    pose_start = 10 if feature_set == "v2" else 4
    pose_size = 6 if feature_set == "v2" else 5
    poses = np.asarray([f[pose_start:pose_start + pose_size] for f, _ in samples], dtype=float)
    calibration_pose = np.median(poses, axis=0).tolist()
    return {"version":MODEL_VERSION,"features":feature_set,"screen":list(screen),"mean":mean.tolist(),"std":std.tolist(),"coef":coef.tolist(),"lambda":lam,"error_px":round(e),"dots":len(targets),"training_samples":[[list(f),list(t)] for f,t in samples],"calibration_pose":calibration_pose,"created":time.time()}

def predict(model,f):
    raw=expand(f,model.get("features","v1")); x=np.column_stack([np.ones(len(raw)),(raw-np.array(model["mean"]))/np.array(model["std"])])
    p=(x@np.array(model["coef"]))[0]; return float(p[0]),float(p[1])

def clean_samples(samples, min_openness=.12, jump_threshold=.08):
    """Drop blinks and fast gaze jumps from (features,target[,openness]) calibration samples."""
    out=[]; prev=None
    for item in samples:
        f,t=item[:2]; op=item[2] if len(item)>2 else 1.
        a=np.asarray(f,float); jump=prev is not None and np.linalg.norm(a[:2]-prev[:2])>jump_threshold
        if op>=min_openness and not jump: out.append((f,t))
        prev=a
    return out

def validate(model,samples):
    grouped={}
    for f,t in samples: grouped.setdefault(tuple(t),[]).append(predict(model,f))
    errs=[]; hits=0; details=[]
    for t,ps in grouped.items():
        p=np.median(np.asarray(ps),axis=0); e=float(np.hypot(*(p-np.asarray(t)))); ok=region(*p,model["screen"])==region(*t,model["screen"])
        errs.append(e); hits+=ok; details.append({"target":list(t),"prediction":p.tolist(),"error_px":e,"region_hit":bool(ok)})
    return {"error_px":float(np.median(errs)) if errs else None,"region_hits":hits,"region_total":len(grouped),"per_target":details}

def region(x,y,screen):
    w,h=screen; col=min(2,max(0,int(3*x/w))); row=min(2,max(0,int(3*y/h)))
    return [["top left","top","top right"],["left","middle","right"],["bottom left","bottom","bottom right"]][row][col]

def fixation(points,t0,t1,radius_px):
    """Return the longest dispersion-bounded fixation in the requested time range."""
    pts=sorted((float(t),float(x),float(y)) for t,x,y in points if t0<=t<=t1)
    best=None
    start=0
    min_x,min_y,max_x,max_y=deque(),deque(),deque(),deque()
    for end, point in enumerate(pts):
        for queue, value, sign, axis in ((min_x, point[1], 1, 1), (max_x, point[1], -1, 1),
                                         (min_y, point[2], 1, 2), (max_y, point[2], -1, 2)):
            while queue and sign * pts[queue[-1]][axis] >= sign * value:
                queue.pop()
            queue.append(end)
        while start < end:
            dx=pts[max_x[0]][1]-pts[min_x[0]][1]
            dy=pts[max_y[0]][2]-pts[min_y[0]][2]
            if dx + dy <= radius_px:
                break
            if min_x[0] == start: min_x.popleft()
            if max_x[0] == start: max_x.popleft()
            if min_y[0] == start: min_y.popleft()
            if max_y[0] == start: max_y.popleft()
            start += 1
        duration=point[0]-pts[start][0]
        if duration >= .2 and (best is None or duration > best["duration"]):
            best={"start_index":start,"end_index":end,"duration":duration}
    if best is None:
        return None
    window=np.asarray([(p[1],p[2]) for p in pts[best["start_index"]:best["end_index"]+1]])
    center=window.mean(axis=0)
    spread=float(np.max(np.linalg.norm(window-center,axis=1)))
    return {"x":float(np.median(window[:,0])),"y":float(np.median(window[:,1])),
            "start":pts[best["start_index"]][0],"end":pts[best["end_index"]][0],
            "samples":len(window),"spread_px":spread}

class Corrector:
    def __init__(self,model): self.base=model; self.model=model; self.samples=[]; self.affine=None
    def add(self,features,target,t):
        pred=self.predict(features)
        if np.hypot(pred[0]-target[0],pred[1]-target[1])>300: return False
        self.samples.append((features,tuple(target),float(t))); self.samples=self.samples[-50:]
        if len(self.samples)>=8:
            try:
                combined=[(f,tuple(p)) for f,p in self.base.get("training_samples",[])]+[(f,p) for f,p,_ in self.samples]
                self.model=fit(combined,self.base["screen"],self.base.get("features","v1"))
            except ValueError: pass
        return True
    def predict(self,features):
        p=np.array(predict(self.model,features))
        if self.affine is not None:
            raw=np.array(predict(self.base,features)); p=np.r_[raw,1.]@self.affine
        return tuple(map(float,p))
    def info(self): return {"samples_used":len(self.samples),"error_px":self.model.get("error_px")}

def radius_px(model,features=None):
    base=float(model.get("error_px") or 200)
    if features is None:return base
    f=np.asarray(features,float)
    ix=10 if model.get("features")=="v2" else 4
    calibration=np.asarray(model.get("calibration_pose", []),dtype=float)
    pose_size=6 if model.get("features")=="v2" else 5
    if len(f)<ix+pose_size or calibration.shape!=(pose_size,):
        return base
    rotation_delta=float(np.linalg.norm(f[ix:ix+3]-calibration[:3]))
    rotation_delta=math.degrees(rotation_delta)
    distance=max(abs(float(calibration[-1])),1e-6)
    translation_delta=float(np.linalg.norm(f[ix+3:ix+pose_size]-calibration[3:]))/distance
    movement=max(rotation_delta/10.0,translation_delta/0.15)
    return base*(1+min(2.,movement))

def save(model,path):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True); tmp=path.with_suffix(".tmp"); tmp.write_text(json.dumps(model),encoding="utf-8"); tmp.replace(path)
def load(path,screen=None):
    try:model=json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError,ValueError):return None
    if not isinstance(model,dict) or model.get("version")!=MODEL_VERSION:return None
    if screen is not None and list(model.get("screen") or [])!=list(screen):return None
    return model

class OneEuro:
    def __init__(self,min_cutoff=.8,beta=.004,d_cutoff=1.): self.min_cutoff,self.beta,self.d_cutoff=min_cutoff,beta,d_cutoff; self.x=self.dx=self.t=None
    @staticmethod
    def _alpha(cutoff,dt): return 1/(1+1/(2*math.pi*cutoff)/dt)
    def __call__(self,x,t):
        x=np.asarray(x,float)
        if self.x is None or self.t is None or t-self.t>1:self.x,self.dx,self.t=x,np.zeros_like(x),t; return x
        dt=max(1e-3,t-self.t); dx=(x-self.x)/dt; a=self._alpha(self.d_cutoff,dt); self.dx=a*dx+(1-a)*self.dx
        a=self._alpha(self.min_cutoff+self.beta*float(np.hypot(*self.dx[:2])),dt); self.x,self.t=a*x+(1-a)*self.x,t; return self.x
