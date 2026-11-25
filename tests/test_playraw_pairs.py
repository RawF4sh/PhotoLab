"""Forum discovery uses first-post rights for each selected asset; examples here are invented."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import playraw_pairs as source


RAW = '<a class="attachment" href="/uploads/short-url/capture.NEF">capture.NEF</a>'
EDIT = ('<a class="lightbox" href="https://d2x313g9lpht1q.cloudfront.net/original/1X/edited.jpeg" '
        'title="edit"><img src="/small.png"></a>')
LICENSE = '<a href="https://creativecommons.org/licenses/by-sa/4.0/">CC BY-SA 4.0</a>'
PLURAL = "These files are licensed"
SINGULAR = "This file is licensed"


def topic(html, reply="", category=30):
    return {"id": 321, "category_id": category, "post_stream": {"posts": [
        {"post_number": 1, "username": "example", "cooked": html},
        {"post_number": 2, "username": "someone-else", "cooked": reply}]}}


def selection(phrase=PLURAL, **extra):
    return dict(scene="mist", raw_grant=phrase, edit_grant=phrase, **extra)


def grant(words=PLURAL):
    return f"<p>{words} {LICENSE}</p>"


def test_only_full_size_original_post_attachments_and_reviewed_grants_enter_plan():
    row = source.reviewed_pair(topic(RAW + EDIT + grant(), reply=EDIT + grant()), selection())
    assert row["scene"] == "mist" and row["page"] == "https://discuss.pixls.us/t/321/1"
    assert [f["role"] for f in row["files"]] == ["before", "after"]
    assert row["files"][0]["url"].endswith("/capture.NEF")
    assert row["files"][1]["url"].endswith("/edited.jpeg")
    assert all(f["author"] == "example" and f["licence"] == "CC BY-SA 4.0" for f in row["files"])
    assert len(row["evidence"]) == 2
    row = source.reviewed_pair(topic(RAW + grant(SINGULAR) + EDIT + grant(SINGULAR)), selection(SINGULAR))
    assert [f["role"] for f in row["files"]] == ["before", "after"]


def test_ambiguous_or_third_party_grants_cannot_authorize_an_author_edit():
    for body in [RAW + EDIT + grant(SINGULAR), RAW + grant(SINGULAR) + EDIT]:
        with pytest.raises(ValueError, match="singular"):
            source.reviewed_pair(topic(body), selection(SINGULAR))
    for body in (RAW + EDIT, RAW + EDIT + "<blockquote>" + grant() + "</blockquote>",
                 RAW + EDIT + grant().replace("/by-sa/", "/by-nc-sa/"),
                 RAW + EDIT + grant() + grant().replace("/by-sa/", "/by-nd/")):
        with pytest.raises(ValueError, match="grant"):
            source.reviewed_pair(topic(body, reply=grant()), selection())
    with pytest.raises(ValueError, match="outside"):
        source.reviewed_pair(topic(RAW + EDIT + grant(), category=2), selection())
    with pytest.raises(ValueError, match="missing"):
        source.reviewed_pair(topic(RAW + EDIT + grant()), selection(raw_index=-1))
    with pytest.raises(ValueError, match="missing"):
        source.reviewed_pair(topic(RAW + EDIT.replace(source.CDN,"elsewhere.example") + grant()), selection())


def test_old_forum_attached_photograph_and_explicit_cc0_declaration():
    photo = ('<img src="//pixls-discuss.s3.amazonaws.com/original/2X/shot.jpg" alt="my edit" '
             'width="690" height="460">')
    logo = photo.replace('width="690"', 'width="80"').replace("shot.jpg", "badge.png")
    row = source.reviewed_pair(topic(logo + photo + RAW + "<p>Both are CC0.</p>"), selection("Both are CC0"))
    assert row["files"][1]["url"].endswith("/shot.jpg") and row["files"][1]["licence"] == "CC0"
    with pytest.raises(ValueError, match="grant"):
        source.reviewed_pair(topic(photo + RAW + "<p>Would both be CC0?</p>"), selection("Both are CC0"))


def test_independently_licensed_reply_preserves_photographer_terms_and_both_credits():
    record = topic(RAW + grant(SINGULAR), reply=EDIT + "<p>License: CC0.</p>")
    chosen = dict(scene="daylight", raw_grant=SINGULAR, edit_grant="License: CC0", edit_post=2)
    row = source.reviewed_pair(record, chosen)
    assert row["files"][1]["licence"] == "CC BY-SA 4.0"
    assert "example (photograph)" in row["files"][1]["author"]
    assert "someone-else (edit)" in row["files"][1]["author"]
    assert row["evidence"][1]["page"].endswith("/2")
    assert row["evidence"][1]["licence_url"].endswith("/zero/1.0/")
    with pytest.raises(ValueError, match="missing"):
        source.reviewed_pair(topic(RAW + grant(SINGULAR), reply=EDIT), chosen)
