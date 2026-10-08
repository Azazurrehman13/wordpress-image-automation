from app.schemas.ai import ImageAnalysis
from app.services.description_parser_service import DescriptionParser
from app.services.image_metadata import build_metadata
from app.services.image_selection_service import SelectionCandidate, eligible
from tests.test_claude_and_selection import GOOD

SRC = "https://original-source.com/product/a"


def test_main_image_uses_title_everywhere():
    md = build_metadata("Galaxy A15 5G", "front_view", True)
    assert md == {k: "Galaxy A15 5G" for k in ("title", "alt_text", "caption", "description")}


def test_gallery_images_get_title_plus_few_words_and_repeats_are_numbered():
    used: dict = {}
    a = build_metadata("Galaxy A15", "side_view", False, used)
    b = build_metadata("Galaxy A15", "side_view", False, used)
    c = build_metadata("Galaxy A15", "back_view", False, used)
    assert a["title"] == "Galaxy A15 - Side View" and b["title"] == "Galaxy A15 - Side View 2" and c["title"] == "Galaxy A15 - Back View"
    assert all(len(set(m.values())) == 1 for m in (a, b, c))


def test_metadata_never_contains_source_url_or_host():
    md = build_metadata("Galaxy A15 https://original-source.com/x", "detail", False, banned=["original-source.com"])
    assert "http" not in md["title"] and "original-source" not in md["title"]


def test_only_the_link_is_removed_description_otherwise_identical():
    html = ('<p>Great phone with [gallery ids="1,2"].</p>\n<p>Original image: <a href="%s">Original Product</a></p>\n'
            '<p>Keep <a href="https://other.com/x">this</a>  and   spacing.</p>\n<p></p>' % SRC)
    out = DescriptionParser().remove_source_links(html, [SRC])
    assert out == ('<p>Great phone with [gallery ids="1,2"].</p>\n<p>Original image: </p>\n'
                   '<p>Keep <a href="https://other.com/x">this</a>  and   spacing.</p>\n<p></p>')


def test_paragraph_holding_only_the_link_is_dropped_and_bare_url_removed():
    html = f'<p>Text</p>\n<p><a href="{SRC}">{SRC}</a></p>\n<p>See {SRC}</p>'
    out = DescriptionParser().remove_source_links(html, [SRC])
    assert out == "<p>Text</p>\n<p>See </p>"


def test_description_without_source_link_is_returned_unchanged():
    html = "<p>Nothing to remove <a href=\"https://other.com\">here</a></p>"
    assert DescriptionParser().remove_source_links(html, [SRC]) == html


def test_image_of_a_different_product_is_never_eligible():
    mk = lambda i, same: SelectionCandidate(i, None, ImageAnalysis(**{**GOOD, "image_id": i, "is_same_product": same, "recommended": True}))
    assert [c.image_id for c in eligible([mk(1, True), mk(2, False)])] == [1]
