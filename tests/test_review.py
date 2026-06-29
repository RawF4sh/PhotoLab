"""M2 review labels: vocabulary coercion + append-only CSV/JSON persistence."""

import os
import tempfile

from autoedit import review


def test_normalize_record_filters_unknown_tags_and_decision():
    r = review.normalize_record({"image": "a.jpg", "decision": "KEEP",
                                 "tags": ["banding", "nonsense", "keep", "good_style"]}, ts=1.0)
    assert r.decision == "keep"
    assert r.tags == ["banding", "good_style"]          # unknown dropped; keep/reject not a tag
    r2 = review.normalize_record({"image": "b.jpg", "decision": "maybe"}, ts=1.0)
    assert r2.decision == ""                             # invalid decision → blank


def test_append_is_append_only_and_roundtrips():
    d = tempfile.mkdtemp()
    out = review.append_labels([{"image": "1.jpg", "decision": "keep", "tags": ["good_style"]},
                                {"image": "2.jpg", "decision": "reject", "tags": ["fake_looking"]}],
                               d, ts=1.0)
    assert out["added"] == 2 and out["total"] == 2
    out2 = review.append_labels([{"image": "3.jpg", "decision": "keep", "source": "edit"}], d, ts=2.0)
    assert out2["added"] == 1 and out2["total"] == 3     # appended, not overwritten

    loaded = review.load_labels(d)
    assert [r["image"] for r in loaded] == ["1.jpg", "2.jpg", "3.jpg"]
    assert os.path.isfile(out["csv"]) and os.path.isfile(out["json"])
    csv_text = open(out["csv"]).read()
    assert csv_text.startswith("image,decision,tags,source,note,timestamp")
    assert "3.jpg,keep,,edit" in csv_text


def test_blank_image_records_skipped():
    d = tempfile.mkdtemp()
    out = review.append_labels([{"image": "", "decision": "keep"}, {"decision": "reject"}], d)
    assert out["added"] == 0
