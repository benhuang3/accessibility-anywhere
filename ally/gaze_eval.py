"""Offline evaluator for numeric eye tracker recordings (python -m ally.gaze_eval report PATHS)."""
import argparse
import csv
import json
from pathlib import Path
import numpy as np
from . import gaze_model as gm

def _frame_before_click(feats, click_time):
    """Return the closest feature frame at or before 100 ms before a click."""
    cutoff = click_time - 0.1
    candidates = [frame for frame in feats if frame[0] <= cutoff]
    if not candidates:
        return None
    frame = max(candidates, key=lambda item: item[0])
    return frame if cutoff - frame[0] <= 0.25 else None

def evaluate(path):
    """Evaluate v1 and v2 models on the held-out phases in one JSONL recording."""
    rows = [json.loads(s) for s in Path(path).read_text(encoding='utf-8').splitlines() if s.strip()]
    meta = next((r for r in rows if r.get('rec') == 'meta'), {})
    screen = tuple(meta.get('screen', (1920, 1080)))
    frames = [r for r in rows if r.get('rec') == 'frame']
    targets = [r for r in rows if r.get('rec') == 'target']
    results = []
    for fs in ('v1', 'v2'):
        feats = []
        for fr in frames:
            z = gm.features_for_model({'features': fs}, fr['lm'], fr.get('bs'), fr.get('mat'), fr['w'], fr['h'])
            if z:
                feats.append((fr['t'], z[0], z[1]))

        def samples(phases):
            """Collect cleaned frame features and targets for the requested phases."""
            out = []
            for target in targets:
                if target.get('phase') not in phases:
                    continue
                for t, f, op in feats:
                    if target['start'] <= t <= target['end']:
                        out.append((f, (target['x'], target['y']), op))
            return gm.clean_samples(out, preserve_openness=True)
        train = samples({'calibration'})
        if len({t for _, t, _ in train}) < 6:
            continue
        model = gm.fit([(f, t) for f, t, _ in train], screen, fs)
        corrector = gm.Corrector(model)
        for click in sorted((r for r in rows if r.get('rec') == 'click'), key=lambda c: c['t']):
            nearest = _frame_before_click(feats, click['t'])
            if nearest:
                corrector.add(nearest[1], (click['x'], click['y']), click['t'])
        for phase in ('validation', 'drift'):
            data = samples({phase})
            plain = gm.validate(model, [(f, t) for f, t, _ in data]) if data else None
            corrected = gm.validate(corrector.model, [(f, t) for f, t, _ in data]) if data else None
            results.append({'file': str(path), 'features': fs, 'phase': phase, 'error_px': plain['error_px'] if plain else None, 'region_hits': plain['region_hits'] if plain else None, 'region_total': plain['region_total'] if plain else None, 'corrected_error_px': corrected['error_px'] if corrected else None, 'corrected_region_hits': corrected['region_hits'] if corrected else None, 'corrected_region_total': corrected['region_total'] if corrected else None})
    return results

def report(paths):
    """Print a Markdown summary and write a CSV beside the evaluated recordings."""
    files = []
    for raw in paths:
        p = Path(raw)
        files.extend(sorted(p.rglob('*.jsonl')) if p.is_dir() else [p])
    rows = [r for p in files for r in evaluate(p)]
    headers = ['file', 'features', 'phase', 'error_px', 'region_hits', 'region_total', 'corrected_error_px', 'corrected_region_hits', 'corrected_region_total']
    print('| ' + ' | '.join(headers) + ' |\n|' + '|'.join(['---'] * len(headers)) + '|')
    for r in rows:
        print('| ' + ' | '.join((str(r.get(k, '')) for k in headers)) + ' |')
    out = files[0].parent / 'gaze_eval.csv' if files else Path('gaze_eval.csv')
    with out.open('w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=headers)
        w.writeheader()
        w.writerows(rows)
    print(f'\nCSV: {out}')
    for fs in ('v1', 'v2'):
        sessions = {r['file'] for r in rows if r['features'] == fs and r['phase'] == 'validation' and (r['error_px'] is not None) and (r['error_px'] <= 150) and (r['region_hits'] >= 8)}
        print(f'{fs} meets the validation bar on {len(sessions)} session(s); recordings are required for a real accuracy conclusion.')

def main():
    """Run the command-line report subcommand."""
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='cmd', required=True)
    rp = sub.add_parser('report')
    rp.add_argument('paths', nargs='+')
    a = ap.parse_args()
    report(a.paths)
if __name__ == '__main__':
    main()
