"""Offline evaluator for numeric eye tracker recordings."""
import argparse
import csv
import json
import math
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


def _summarize(points, screen):
    """Summarize target/prediction pairs using the public validation metrics."""
    errors = []
    hits = 0
    for target, prediction in points:
        errors.append(float(np.linalg.norm(np.asarray(prediction) - np.asarray(target))))
        hits += gm.region(*prediction, screen) == gm.region(*target, screen)
    return {'error_px': float(np.median(errors)) if errors else None,
            'region_hits': hits, 'region_total': len(points)}


def _grouped_samples(targets, feats, phase, min_openness=0.12,
                     calibration_pose=None, feature_set='v1', reject_head_motion=False):
    groups = {}
    for target in targets:
        if target.get('phase') != phase:
            continue
        items = [(f, (target['x'], target['y']), op) for t, f, op in feats
                 if target['start'] <= t <= target['end']]
        if (reject_head_motion and calibration_pose is not None
                and gm.target_head_moved([item[0] for item in items], calibration_pose, feature_set)):
            continue
        clean = gm.clean_samples(items, min_openness=min_openness, preserve_openness=True)
        if clean:
            groups.setdefault(tuple((target['x'], target['y'])), []).extend(clean)
    return groups


def evaluate(path):
    """Evaluate validation, drift, leave-one-target-out retuning, and click correction."""
    rows = [json.loads(s) for s in Path(path).read_text(encoding='utf-8').splitlines() if s.strip()]
    meta = next((r for r in rows if r.get('rec') == 'meta'), {})
    screen = tuple(meta.get('screen', (1920, 1080)))
    frames = [r for r in rows if r.get('rec') == 'frame']
    targets = [r for r in rows if r.get('rec') == 'target']
    results = []
    screen_mm = meta.get('screen_mm', (344.0, 193.0))
    for fs in ('v1', 'v2', 'v3', 'v4'):
        feats = []
        for fr in frames:
            z = gm.features_for_model({'features': fs, 'screen_mm': screen_mm}, fr['lm'], fr.get('bs'), fr.get('mat'), fr['w'], fr['h'])
            if z:
                feats.append((fr['t'], z[0], z[1]))
        raw_calibration = [(f, (target['x'], target['y']), op)
                           for target in targets if target.get('phase') == 'calibration'
                           for t, f, op in feats if target['start'] <= t <= target['end']]
        cutoff = gm.openness_cutoff(raw_calibration)
        pose_start = 10 if fs == 'v2' else 4
        pose_size = 6 if fs == 'v2' else 5
        calibration_frames = [f for t, f, _ in feats
                              if any(target.get('phase') == 'calibration'
                                     and target['start'] <= t <= target['end'] for target in targets)]
        calibration_pose = np.median([f[pose_start:pose_start + pose_size]
                                      for f in calibration_frames], axis=0).tolist()
        train = []
        train_weights = []
        for target in targets:
            if target.get('phase') != 'calibration':
                continue
            items = [(f, (target['x'], target['y']), op) for t, f, op in feats
                     if target['start'] <= t <= target['end']]
            moved = gm.target_head_moved([item[0] for item in items], calibration_pose, fs)
            clean = gm.clean_samples(items, min_openness=cutoff, preserve_openness=True)
            train.extend((f, point) for f, point, _ in clean)
            train_weights.extend([0.9 if moved else 1.0] * len(clean))
        if len({tuple(point) for _, point in train}) < 6:
            continue
        model = gm.fit(train, screen, fs, cutoff, train_weights, screen_mm=screen_mm)
        model['screen_mm'] = list(screen_mm)
        corrector = gm.Corrector(model)
        for click in sorted((r for r in rows if r.get('rec') == 'click'), key=lambda c: c['t']):
            nearest = _frame_before_click(feats, click['t'])
            if nearest:
                corrector.add(nearest[1], (click['x'], click['y']), click['t'])

        validation = _grouped_samples(targets, feats, 'validation', cutoff)
        drift = _grouped_samples(targets, feats, 'drift', cutoff)
        validation_points = []
        for target, group in validation.items():
            pred = np.median([gm.predict(model, f) for f, _, _ in group], axis=0)
            validation_points.append((target, pred))
        drift_points = []
        retuned_points = []
        click_points = []
        drift_items = list(drift.items())
        for index, (target, group) in enumerate(drift_items):
            pred = np.median([gm.predict(model, f) for f, _, _ in group], axis=0)
            drift_points.append((target, pred))
            other = [(f, t) for i, (_, other_group) in enumerate(drift_items) if i != index
                     for f, t, _ in other_group]
            tuned = gm.retune(model, other) if len({tuple(t) for _, t in other}) >= 3 else model
            retuned = np.median([gm.predict(tuned, f) for f, _, _ in group], axis=0)
            retuned_points.append((target, retuned))
            corrected = np.median([corrector.predict(f) for f, _, _ in group], axis=0)
            click_points.append((target, corrected))
        summaries = {
            'validation': _summarize(validation_points, screen),
            'drift': _summarize(drift_points, screen),
            'drift_retuned': _summarize(retuned_points, screen),
            'drift_click_corrected': _summarize(click_points, screen),
        }
        retuned = summaries['drift_retuned']
        clicked = summaries['drift_click_corrected']
        for phase in ('validation', 'drift'):
            summary = summaries[phase]
            results.append({'file': str(path), 'features': fs, 'phase': phase, **summary,
                            'drift_without_retune_error_px': summaries['drift']['error_px'] if phase == 'drift' else None,
                            'retuned_error_px': retuned['error_px'] if phase == 'drift' else None,
                            'retuned_region_hits': retuned['region_hits'] if phase == 'drift' else None,
                            'retuned_region_total': retuned['region_total'] if phase == 'drift' else None,
                            'click_error_px': clicked['error_px'] if phase == 'drift' else None,
                            'click_region_hits': clicked['region_hits'] if phase == 'drift' else None,
                            'click_region_total': clicked['region_total'] if phase == 'drift' else None})
    return results


def _meets(row, prefix=''):
    error = row.get(prefix + 'error_px')
    total = row.get(prefix + 'region_total') or 0
    needed = math.ceil(0.89 * total)
    return error is not None and error <= 150 and total > 0 and row.get(prefix + 'region_hits', 0) >= needed


def report(paths, out=None):
    """Print a Markdown summary and write a CSV to out or the current folder."""
    files = []
    for raw in paths:
        p = Path(raw)
        files.extend(sorted(p.rglob('*.jsonl')) if p.is_dir() else [p])
    rows = [r for p in files for r in evaluate(p)]
    headers = ['file', 'features', 'phase', 'error_px', 'region_hits', 'region_total',
               'drift_without_retune_error_px',
               'retuned_error_px', 'retuned_region_hits', 'retuned_region_total',
               'click_error_px', 'click_region_hits', 'click_region_total']
    indexed = {(row['file'], row['features'], row['phase']): row for row in rows}
    summary_headers = ['session', 'features', 'validation', 'drift without re-tune', 'drift with re-tune']
    print('| ' + ' | '.join(summary_headers) + ' |\n|' + '|'.join(['---'] * len(summary_headers)) + '|')
    for path in files:
        for fs in ('v1', 'v2', 'v3', 'v4'):
            validation = indexed.get((str(path), fs, 'validation'), {})
            drift = indexed.get((str(path), fs, 'drift'), {})
            def metric(row, error_key='error_px'):
                error = row.get(error_key)
                return '' if error is None else f"{error:.1f} ({row.get('region_hits', 0)}/{row.get('region_total', 0)})"
            print('| ' + ' | '.join((path.name, fs, metric(validation), metric(drift),
                                     metric(drift, 'retuned_error_px'))) + ' |')
    output = Path(out) if out else Path.cwd() / 'gaze_eval.csv'
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)
    print(f'\nCSV: {output}')
    phases = [('validation', 'validation', ''), ('drift', 'drift', ''),
              ('drift', 'drift after retune', 'retuned_')]
    for fs in ('v1', 'v2', 'v3', 'v4'):
        for phase, label, prefix in phases:
            sessions = {row['file'] for row in rows if row['features'] == fs and row['phase'] == phase and _meets(row, prefix)}
            print(f'{fs} meets the bar on {len(sessions)} session(s) for {label}.')


def main():
    """Run the command-line report subcommand."""
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='cmd', required=True)
    rp = sub.add_parser('report')
    rp.add_argument('paths', nargs='+')
    rp.add_argument('--out', default=None, help='CSV output path (default: ./gaze_eval.csv)')
    args = ap.parse_args()
    report(args.paths, args.out)


if __name__ == '__main__':
    main()
