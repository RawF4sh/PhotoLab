"""Build a blind current-vs-Natural-clean gallery and an honest coverage report.

Example: PYTHONPATH=src python scripts/natural_review.py PHOTO_FOLDER OUTPUT --category landscape
The gallery uses existing blind-test voting and scoring; it never changes application defaults.
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import statistics

import blindtest
from autoedit.pipeline import Pipeline
from autoedit.segment import CachingSegmenter

CATEGORIES = ('landscape', 'portrait', 'indoor', 'product', 'phone', 'unclassified')


def summarize(rows, category, rendered, failed):
    """Tone checks are measurements, not a human preference or colour-accuracy score."""
    methods = {}
    flag = lambda value: str(value).casefold() in ('true', '1')
    for method in sorted({row['method'] for row in rows}):
        group = [row for row in rows if row['method'] == method]
        ev = [float(row['ev_vs_src']) for row in group if row.get('ev_vs_src') not in (None, '')]
        methods[method] = {
            'photos': len(group), 'dark_made_darker': sum(flag(row.get('dark_made_darker')) for row in group),
            'pushed_into_highlights': sum(flag(row.get('bright_pushed_to_clip')) for row in group),
            'pushed_into_shadows': sum(flag(row.get('shadow_pushed_to_clip')) for row in group),
            'median_exposure_change_ev': statistics.median(ev) if ev else None,
        }
    return {
        'photos': rendered, 'failed': failed, 'complete': rendered > 0 and failed == 0,
        'coverage': {key: rendered if key == category else 0 for key in CATEGORIES},
        'coverage_basis': 'Category supplied by the reviewer for this source set; no automatic genre claim.',
        'measurements': methods, 'preference': 'Awaiting blind votes; no winner inferred from tone metrics.',
        'colour_accuracy': 'Not measured: no labelled skin or neutral colour target annotations supplied.',
        'default_changed': False,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('photos')
    parser.add_argument('out')
    parser.add_argument('--category', choices=CATEGORIES, default='unclassified')
    parser.add_argument('--backend', choices=['stub', 'sam3'], default='stub')
    parser.add_argument('--limit', type=int, default=16)
    parser.add_argument('--max-px', type=int, default=1200)
    parser.add_argument('--seed', type=int, default=55)
    args = parser.parse_args(argv)
    if not 1 <= args.limit <= 200 or not 256 <= args.max_px <= 2560:
        parser.error('limit must be 1–200 and max-px must be 256–2560')
    source, destination = Path(args.photos).resolve(), Path(args.out).resolve()
    if not source.is_dir():
        parser.error('source folder does not exist')
    if source == destination or destination in source.parents or source in destination.parents:
        parser.error('keep the report in a separate folder from the source photos')
    if destination.exists() and any(destination.iterdir()):
        parser.error('choose an empty output folder; existing reports are never overwritten')
    cfg = blindtest.make_config(backend=args.backend).model_copy(update={
        'look': 'natural', 'decider': 'rules', 'allow_cloud': False, 'translator': 'off',
        'api_key': None, 'history': False,
    })
    candidate = cfg.model_copy(update={'look': 'natural_clean'})
    current_pipe, candidate_pipe = Pipeline.build(cfg), Pipeline.build(candidate)
    current_pipe.segmenter = CachingSegmenter(current_pipe.segmenter)
    candidate_pipe.segmenter = current_pipe.segmenter
    destination.mkdir(parents=True, exist_ok=True)
    result = blindtest.render(str(source), str(destination / 'gallery'), cfg,
                             methods=('current', 'candidate'),
                             pipelines={'current': current_pipe, 'candidate': candidate_pipe},
                             limit=args.limit, max_px=args.max_px, seed=args.seed)
    metric_file = destination / 'gallery' / 'metrics.csv'
    rows = []
    if metric_file.exists():
        with metric_file.open(newline='', encoding='utf-8') as stream:
            rows = list(csv.DictReader(stream))
    report = summarize(rows, args.category, result['photos'], result['failed'])
    report['backend'] = args.backend
    report['region_evidence'] = ('Heuristic demo masks; this does not validate object-selection quality.'
                                 if args.backend == 'stub' else 'SAM 3 local segmentation; review masks alongside the photographs.')
    (destination / 'report.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    text = ('# Natural-edit comparison\n\n'
            f"Rendered {report['photos']} pairs; {report['failed']} failures. Declared category: {args.category}.\n\n"
            'Open [the blind gallery](gallery/index.html), choose the result you prefer (or no preference), '
            'and download the votes. The gallery hides which look made each version.\n\n'
            'The candidate reduces added local contrast and removes the unconditional finishing curve. '
            'Exposure and colour rules stay the same. No default has changed.\n\n'
            'Tone checks do not establish that people prefer a result. Skin and exact colour accuracy '
            'need labelled real photographs; categories absent from this report remain untested.\n\n'
            'After voting, score the exported CSV with `python scripts/blindtest.py --score PATH_TO_RESULTS_CSV`.\n')
    (destination / 'review.md').write_text(text, encoding='utf-8')
    print(f"Review: {destination / 'gallery' / 'index.html'}")
    print(f"Coverage and measurements: {destination / 'report.json'}")
    return int(not report['complete'])


if __name__ == '__main__':
    raise SystemExit(main())
