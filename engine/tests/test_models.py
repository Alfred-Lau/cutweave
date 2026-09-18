"""时间线模型单元测试。"""
import pytest
from pydantic import ValidationError

from app.models import Canvas, Draft, Material, Segment, TextStyle, Transform


def test_segment_time_range_validated():
    with pytest.raises(ValidationError):
        Segment(material_id="m1", start=5, end=5)
    with pytest.raises(ValidationError):
        Segment(material_id="m1", start=6, end=5)
    s = Segment(material_id="m1", start=1.5, end=4.0)
    assert s.duration == pytest.approx(2.5)


def test_transform_bounds():
    with pytest.raises(ValidationError):
        Transform(scale=0)
    with pytest.raises(ValidationError):
        Segment(material_id="m1", start=0, end=1, alpha=1.5)


def test_draft_ensure_track_and_duration():
    d = Draft(name="t", canvas=Canvas(width=720, height=1280))
    t0 = d.ensure_track("video", level=0)
    t0.segments.append(Segment(material_id="m", start=0, end=8))
    t1 = d.ensure_track("video", level=1)
    assert t1 is not t0
    t1.segments.append(Segment(material_id="m", start=2, end=5))
    d.ensure_track("text").segments.append(
        Segment(material_id="m", start=0, end=10))
    assert d.duration() == pytest.approx(10)
    s = d.summary()
    assert s["segment_count"] == 3 and s["duration"] == 10


def test_material_and_find_segment():
    d = Draft()
    m = Material(kind="text", text="hi", style=TextStyle(font_size=48))
    d.materials.append(m)
    seg = Segment(material_id=m.material_id, start=0, end=1)
    d.ensure_track("text").segments.append(seg)
    track, found = d.find_segment(seg.segment_id)
    assert track.type == "text" and found is seg
    assert d.material(m.material_id).text == "hi"
    with pytest.raises(KeyError):
        d.find_segment("nope")
