"""Tests for the per-tool mascot generator."""

from planckbot.ui.mascots import (
    ACCESSORIES,
    mascot_svg,
    style_for,
)


def test_same_name_same_style():
    a = style_for("file_search")
    b = style_for("file_search")
    assert a == b


def test_different_names_different_styles():
    a = style_for("file_search")
    b = style_for("bash")
    c = style_for("read_file")
    # At least one of the style fields should differ across names
    assert (a.body_hue, a.accessory) != (b.body_hue, b.accessory)
    assert (a.body_hue, a.accessory) != (c.body_hue, c.accessory)


def test_accessory_keyword_matching():
    assert style_for("file_search").accessory == "magnifier"
    assert style_for("grep").accessory == "magnifier"
    assert style_for("read_file").accessory == "scroll"
    assert style_for("Bash").accessory == "wrench"
    assert style_for("Write").accessory == "quill"
    assert style_for("Glob").accessory == "net"


def test_default_accessory():
    # Unknown tool name falls back to gear
    s = style_for("zxqxy_unknown_widget")
    assert s.accessory == "gear"


def test_every_accessory_id_has_svg():
    all_ids = {acc for _, acc in [(None, v) for v in [
        "magnifier", "scroll", "wrench", "quill", "net", "flask",
        "bucket", "antenna", "rocket", "star", "gear",
    ]]}
    for acc_id in all_ids:
        assert acc_id in ACCESSORIES, f"missing SVG for {acc_id}"


def test_mascot_svg_is_valid_standalone():
    svg = mascot_svg("file_search", size=96)
    assert svg.startswith("<svg")
    assert svg.rstrip().endswith("</svg>")
    assert 'width="96"' in svg
    assert 'height="96"' in svg
    assert 'viewBox="0 0 100 100"' in svg


def test_mascot_svg_embeds_tool_name_in_aria():
    svg = mascot_svg("my/tool<weird>")
    # Must HTML-escape the name in aria-label
    assert "&lt;" in svg and "&gt;" in svg


def test_mascot_ids_unique_per_tool():
    a = mascot_svg("tool_a")
    b = mascot_svg("tool_b")
    # Gradients have tool-specific ids so two mascots on the same page
    # don't steal each other's gradients.
    import re
    ids_a = set(re.findall(r'id="(bodyg|orbg|glow)-([a-f0-9]+)"', a))
    ids_b = set(re.findall(r'id="(bodyg|orbg|glow)-([a-f0-9]+)"', b))
    # The hex suffixes differ between the two tools
    suffixes_a = {suf for _, suf in ids_a}
    suffixes_b = {suf for _, suf in ids_b}
    assert suffixes_a.isdisjoint(suffixes_b)


def test_style_fields_in_bounds():
    s = style_for("any_name")
    # Body stays in the brand's teal band (145–185) for family resemblance.
    assert 145 <= s.body_hue <= 185
    assert 0 <= s.orb_hue < 360
    assert -1 <= s.eye_shine_dx <= 1
    assert 1 <= s.smile_curve <= 4
