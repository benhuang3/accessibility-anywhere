"""Offline replay of face-sensing recordings. Reads JSONL numbers; writes only under --out."""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from .detectors import (Baseline, GeometricSquintDetector, HybridSquintDetector, LeanDetector,
                       DistanceLeanDetector, HybridLeanDetector, RelativeSquintDetector, SquintDetector, Tuning)
from .features import extract, robust_baseline

SQUINT = {"v1": SquintDetector, "relative": RelativeSquintDetector, "geometric": GeometricSquintDetector,
          "hybrid": HybridSquintDetector}
LEAN = {"v1": LeanDetector, "distance": DistanceLeanDetector, "hybrid": HybridLeanDetector}


def read_recording(path):
    """Read one recorder file into meta, timestamped numeric frames and labelled intervals."""
    meta, frames, segments = {}, [], []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        try: row=json.loads(line)
        except ValueError: continue
        typ=row.get("rec", row.get("type", row.get("kind", "")))
        if typ=="meta": meta=row
        elif typ == "frame" or (typ == "helper" and "t" in row and ("f" in row or "lm" in row)):
            f=row.get("f", row)
            frames.append({"t":float(row.get("t",0)), "f":f, "raw":row if "lm" in row else f})
        elif typ in ("label", "target"):
            expect=row.get("expect", row.get("pose", "none"))
            start=float(row.get("start", row.get("t",0))); end=float(row.get("end",start))
            if typ=="target":
                expect="lean" if expect=="close" else "none"
            segments.append({"name":row.get("name",row.get("pose",expect)),"start":start,"end":end,"expect":expect,
                             "phase":row.get("phase",""),"pose":row.get("pose","normal")})
    frames.sort(key=lambda x:x["t"])
    return {"meta":meta,"frames":frames,"segments":segments}


def _legacy_baseline(frames):
    calibration=[]
    for item in frames:
        f=item["f"]; raw=item["raw"]
        if "squint_l" in f and "width" in f: calibration.append(f); continue
        bs=raw.get("bs",{}) if isinstance(raw,dict) else {}
        if bs:
            calibration.append({"squint_l":bs.get("eyeSquintLeft",.1),"squint_r":bs.get("eyeSquintRight",.1),
                               "width":.3,"blink":(bs.get("eyeBlinkLeft",0)+bs.get("eyeBlinkRight",0))/2,
                               "smile":0,"yaw":0,"pitch":0,"light":100})
    if not calibration: return Baseline(.1,.3)
    try:return Baseline.from_samples(calibration,0)
    except ValueError:return Baseline(.1,.3)


def evaluate(record, kind="squint", tuning=None):
    frames=record["frames"]
    if not frames:return []
    labels=[s for s in record["segments"] if s["expect"] in ("squint","lean","natural","none","struggle")]
    # Calibration-only frames form the raw feature baseline when available.
    calibration=[]
    for item in frames:
        if any((s.get("phase")=="calibration" and s.get("pose") not in ("back","close") or
                s.get("expect")=="baseline") and
               s["start"]<=item["t"]<=s["end"] for s in record["segments"]):
            raw=item["raw"]
            if isinstance(raw,dict) and len(raw.get("lm",[]))>=478:
                try: calibration.append(extract(raw["lm"],raw.get("bs"),raw.get("mat"),raw.get("w",1),raw.get("h",1)))
                except (IndexError,TypeError,ValueError): pass
    raw_base=robust_baseline(calibration)
    calibration_items=[item for item in frames if any((s.get("phase")=="calibration" and s.get("pose") not in ("back","close")
                       or s.get("expect")=="baseline") and s["start"]<=item["t"]<=s["end"] for s in record["segments"])]
    baseline=_legacy_baseline(calibration_items or frames)
    rows=[]
    for name,cls in (SQUINT.items() if kind=="squint" else LEAN.items()):
        det=cls(Tuning(**(tuning or {}))); signals=[]
        for item in frames:
            f=item["f"]
            if ("width" not in f or "squint_l" not in f) and item["raw"].get("lm"):
                raw=item["raw"]; bs=raw.get("bs",{}); f={**f,"eyeSquint":(bs.get("eyeSquintLeft",0)+bs.get("eyeSquintRight",0))/2,
                    "squint_l":bs.get("eyeSquintLeft",0),"squint_r":bs.get("eyeSquintRight",0),
                    "eyeSquintLeft":bs.get("eyeSquintLeft",0),"eyeSquintRight":bs.get("eyeSquintRight",0),
                    "eyeBlink":(bs.get("eyeBlinkLeft",0)+bs.get("eyeBlinkRight",0))/2,
                    "blink":(bs.get("eyeBlinkLeft",0)+bs.get("eyeBlinkRight",0))/2,
                    "smile":(bs.get("mouthSmileLeft",0)+bs.get("mouthSmileRight",0))/2,
                    "yaw":0,"pitch":0,"width":raw.get("w",1)*.3,"light":100,
                    "head_distance":sum(float(raw.get("mat",[0]*16)[i])**2 for i in (3,7,11))**.5}
                if len(raw.get("lm",[]))>=478:
                    try:f.update(extract(raw["lm"],bs,raw.get("mat"),raw.get("w",1),raw.get("h",1)))
                    except (IndexError,TypeError,ValueError):pass
            elif "squint_l" not in f and "squint_l" in f.get("f",{}): f=f["f"]
            if "squint_l" not in f:
                f={**f,"squint_l":float(f.get("eyeSquintLeft",f.get("squintLeft",.1))),
                   "squint_r":float(f.get("eyeSquintRight",f.get("squintRight",.1))),
                   "smile":float(f.get("mouthSmile",0)),"blink":float(f.get("eyeBlink",0)),
                   "yaw":float(f.get("head_yaw",0)),"pitch":float(f.get("head_pitch",0)),
                   "width":float(f.get("face_width",.3)),"light":100}
            out=det.feed(f,item["t"],raw_base if name!="v1" and raw_base else baseline)
            if out:signals.append(out)
        for seg in labels:
            is_positive=seg["expect"]==kind
            hits=[s for s in signals if seg["start"]<=s.ts<=seg["end"]]
            if is_positive or seg["expect"] in ("natural","none"):
                rows.append({"person":record["meta"].get("alias","unknown"),"method":name,"kind":kind,
                             "segment":seg["name"],"expect":seg["expect"],"hit":int(bool(hits)) if is_positive else "",
                             "false_positive":int(bool(hits)) if not is_positive else "",
                             "group_eyes":record["meta"].get("eyes","unknown"),
                             "group_age":record["meta"].get("age","unknown"),
                             "group_glasses":record["meta"].get("glasses","unknown"),
                             "group_light":record["meta"].get("light","unknown"),
                             "latency":round(hits[0].ts-seg["start"],3) if hits and is_positive else ""})
        if any(isinstance(x["raw"],dict) and "lm" in x["raw"] for x in frames):
            # Gaze data is explicitly all-negative for squint; include signals in gaps between targets.
            if kind=="squint":
                rows.append({"person":record["meta"].get("alias","unknown"),"method":name,"kind":kind,
                             "segment":"whole gaze recording","expect":"none","hit":"",
                             "false_positive":int(bool(signals)),"latency":"",
                             "group_eyes":record["meta"].get("eyes","unknown"),
                             "group_age":record["meta"].get("age","unknown"),
                             "group_glasses":record["meta"].get("glasses","unknown"),
                             "group_light":record["meta"].get("light","unknown")})
    return rows


def discover(paths):
    for value in paths:
        p=Path(value)
        if p.is_dir(): yield from sorted(p.rglob("*.jsonl"))
        elif p.is_file(): yield p


def report(paths, out):
    out=Path(out);out.mkdir(parents=True,exist_ok=True); rows=[]
    for path in discover(paths):
        rec=read_recording(path)
        rows.extend(evaluate(rec,"squint"));rows.extend(evaluate(rec,"lean"))
    fields=["person","method","kind","segment","expect","hit","false_positive","latency",
            "group_eyes","group_age","group_glasses","group_light"]
    with (out/"report.csv").open("w",newline="",encoding="utf-8") as f:
        writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader();writer.writerows(rows)
    by={}
    for r in rows:
        key=(r["kind"],r["method"]); a=by.setdefault(key,[0,0,0]);
        if r["hit"]!="":a[0]+=int(r["hit"]);a[1]+=1
        if r["false_positive"]!="":a[2]+=int(r["false_positive"])
    people=sorted({r["person"] for r in rows})
    lines=["# Offline senses benchmark","","Candidate comparison from numeric-only recordings.","",
           "| Signal | Method | Hits / positives | False positive segments |","|---|---|---:|---:|"]
    lines += [f"| {k} | {m} | {a[0]} / {a[1]} | {a[2]} |" for (k,m),a in sorted(by.items())]
    lines += ["", "False positives per minute of recorded no-struggle time:", "",
              "| Signal | Method | FP / minute |", "|---|---|---:|"]
    durations={}
    for path in discover(paths):
        rec=read_recording(path)
        if not rec["frames"]: continue
        span=max(x["t"] for x in rec["frames"])-min(x["t"] for x in rec["frames"])
        durations[rec["meta"].get("alias","unknown")]=durations.get(rec["meta"].get("alias","unknown"),0)+span
    minutes=sum(durations.values())/60
    for (k,m),a in sorted(by.items()):
        lines.append(f"| {k} | {m} | {a[2]/minutes:.3f} |" if minutes else f"| {k} | {m} | n/a |")
    lines += ["", "## Per person", "", "| Person | Signal | Method | Hits / positives | False positives |", "|---|---|---|---:|---:|"]
    pby={}
    for row in rows:
        key=(row["person"],row["kind"],row["method"]); a=pby.setdefault(key,[0,0,0])
        if row["hit"]!="":a[0]+=int(row["hit"]);a[1]+=1
        if row["false_positive"]!="":a[2]+=int(row["false_positive"])
    lines += [f"| {p} | {k} | {m} | {a[0]} / {a[1]} | {a[2]} |" for (p,k,m),a in sorted(pby.items())]
    lines += ["", "## Fairness groups", "", "False positive segment rate and miss rate; unknown metadata remains a visible group.",
              "", "| Signal | Method | Attribute | Group | FP rate | Miss rate |", "|---|---|---|---|---:|---:|"]
    for k,m in sorted(by):
        group_rows=[r for r in rows if r["kind"]==k and r["method"]==m]
        for attr in ("eyes","age","glasses","light"):
            groups={}
            for row in group_rows:
                value=row["group_"+attr]; a=groups.setdefault(value,[0,0,0,0])
                if row["false_positive"]!="":a[0]+=int(row["false_positive"]);a[1]+=1
                if row["hit"]!="":a[2]+=1-int(row["hit"]);a[3]+=1
            rates=[]
            for group,a in sorted(groups.items()):
                fp=a[0]/a[1] if a[1] else 0; miss=a[2]/a[3] if a[3] else 0
                rates.append(fp)
                lines.append(f"| {k} | {m} | {attr} | {group} | {fp:.3f} | {miss:.3f} |")
            if rates: lines.append(f"| {k} | {m} | {attr} | worst-to-best gap | {max(rates)-min(rates):.3f} |  |")
    known={a:sorted({r["group_"+a] for r in rows if r["group_"+a]!="unknown"}) for a in ("eyes","age","glasses","light")}
    lines += ["", "## Recommendation and limits", "",
              "Keep v1 selected until labeled, raw-number recordings show reliable candidate performance. "
              "The new relative and geometric results here are not a basis to enable squint sensing: "
              "senses recordings in this set lack raw landmarks/blendshapes, and gaze recordings contain only negative squint examples.",
              "", f"This run covers {len(people)} people: {', '.join(people) or 'none'}. "
              f"Annotated groups present: eyes={', '.join(known['eyes']) or 'none'}, age={', '.join(known['age']) or 'none'}, "
              f"glasses={', '.join(known['glasses']) or 'none'}, light={', '.join(known['light']) or 'none'}. "
              "Unknown groups cannot support fairness conclusions.", "",
              "## Camera helper payload", "",
              "For raw candidates, each face signals line must include `lm` (478 normalized [x,y,z] landmark triples), "
              "`bs` (MediaPipe blendshape-name to score mapping), `mat` (16 row-major face-matrix numbers), and `w`, `h` "
              "(frame dimensions). No image, pixel buffer, or video is needed or accepted by this offline pipeline."]
    (out/"report.md").write_text("\n".join(lines)+"\n",encoding="utf-8")
    return rows


def tune(paths):
    """Leave one person out, selecting the loosest zero-training-FP threshold per family."""
    records=[(p,read_recording(p)) for p in discover(paths)]
    aliases=sorted({r["meta"].get("alias",p.stem) for p,r in records})
    if len(aliases)<3:
        raise SystemExit("Refusing to recommend values from fewer than 3 people.")
    grids={"relative": [{"relative_squint_weak":w,"relative_squint_strong":s}
                         for w,s in ((.2,.35),(.25,.4),(.3,.45),(.35,.5))],
           "geometric": [{"geometric_aperture_drop":d,"geometric_lower_rise":r}
                         for d,r in ((.10,.025),(.12,.035),(.15,.04),(.18,.05))],
           "distance": [{"lean_ratio":v,"lean_weak":v-.05} for v in (1.10,1.15,1.20,1.25)]}
    methods={"squint":("relative","geometric"),"lean":("distance",)}
    results=[]
    for heldout in aliases:
        training=[(p,r) for p,r in records if r["meta"].get("alias",p.stem)!=heldout]
        test=[(p,r) for p,r in records if r["meta"].get("alias",p.stem)==heldout]
        for kind, names in methods.items():
            for method in names:
                best=None
                for values in grids[method]:
                    train_rows=[x for p,r in training for x in evaluate(r,kind,values) if x["method"]==method]
                    fps=sum(int(x["false_positive"] or 0) for x in train_rows)
                    hits=sum(int(x["hit"] or 0) for x in train_rows)
                    if fps==0 and (best is None or hits>best[0]): best=(hits,values)
                chosen=best[1] if best else grids[method][0]
                test_rows=[x for p,r in test for x in evaluate(r,kind,chosen) if x["method"]==method]
                hit=sum(int(x["hit"] or 0) for x in test_rows)
                total=sum(x["hit"]!="" for x in test_rows)
                fp=sum(int(x["false_positive"] or 0) for x in test_rows)
                results.append((heldout,kind,method,chosen,hit,total,fp))
    print("| Held-out person | Signal | Method | Selected training values | Held-out hits/positive | FP segments |")
    print("|---|---|---|---|---:|---:|")
    for person,kind,method,values,hits,total,fp in results:
        print(f"| {person} | {kind} | {method} | `{json.dumps(values,sort_keys=True)}` | {hits}/{total} | {fp} |")
    return results


def main(argv=None):
    parser=argparse.ArgumentParser();sub=parser.add_subparsers(dest="cmd",required=True)
    r=sub.add_parser("report");r.add_argument("paths",nargs="+");r.add_argument("--out",required=True)
    t=sub.add_parser("tune");t.add_argument("paths",nargs="+")
    args=parser.parse_args(argv)
    if args.cmd=="report": report(args.paths,args.out)
    else: tune(args.paths)


if __name__=="__main__":main()
