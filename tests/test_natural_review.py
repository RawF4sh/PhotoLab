import importlib.util
from pathlib import Path
import sys


def _module():
    scripts = Path(__file__).resolve().parent.parent / 'scripts'
    sys.path.insert(0, str(scripts))
    try:
        spec = importlib.util.spec_from_file_location('natural_review_test', scripts / 'natural_review.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(scripts))


def test_natural_review_keeps_failures_and_untested_categories_visible():
    report = _module().summarize([
        {'method': 'current', 'ev_vs_src': '.1', 'dark_made_darker': 'False'},
        {'method': 'candidate', 'ev_vs_src': '.2', 'bright_pushed_to_clip': 'True'},
    ], 'landscape', 1, 1)
    assert not report['complete'] and report['failed'] == 1
    assert report['coverage']['landscape'] == 1
    assert report['coverage']['portrait'] == report['coverage']['product'] == 0
    assert report['measurements']['candidate']['pushed_into_highlights'] == 1
    assert report['measurements']['current']['dark_made_darker'] == 0
    assert not report['default_changed']
    assert 'Awaiting blind votes' in report['preference']
    assert 'Not measured' in report['colour_accuracy']


def test_natural_review_empty_is_not_complete():
    assert not _module().summarize([], 'unclassified', 0, 0)['complete']
