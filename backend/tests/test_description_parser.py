from app.services.description_parser_service import DescriptionParser
from app.services.source_url_service import SourceUrlService

P = DescriptionParser()
HTML = """
<p>Great phone. Follow us <a href="https://facebook.com/shop">Facebook</a> and
<a href="https://instagram.com/shop">Instagram</a>.</p>
<p>Original image: <a href="https://original-source.com/product/samsung-galaxy-a15?utm_source=x">Original Product</a></p>
<p><a href="https://target-site.com/privacy-policy">Privacy Policy</a> https://target-site.com/contact</p>
"""


def test_extract_urls_finds_anchors_and_plain_text():
    urls = {u.url for u in P.extract_urls(HTML + "see https://plain.example.org/p/abc.")}
    assert "https://original-source.com/product/samsung-galaxy-a15?utm_source=x" in urls
    assert "https://plain.example.org/p/abc" in urls


def test_identifies_original_not_social_or_legal():
    cands = P.identify_source_urls(P.extract_urls(HTML), "Samsung Galaxy A15", "target-site.com")
    hosts = [c.url for c in cands]
    assert all("facebook" not in h and "instagram" not in h and "target-site" not in h for h in hosts)
    best = P.select_original_source_url(cands).selected
    assert best.url == "https://original-source.com/product/samsung-galaxy-a15"  # tracking param stripped


def test_not_the_first_url():
    html = '<a href="https://blog.example.com/news">News</a> Source: <a href="https://shop.example.net/products/galaxy-a15">x</a>'
    det = SourceUrlService().detect(html, "Galaxy A15", "my.com")
    assert det.selected.url == "https://shop.example.net/products/galaxy-a15"


def test_no_source_found():
    det = SourceUrlService().detect('<a href="https://facebook.com/x">fb</a> Plain text only', "X", "my.com")
    assert det.selected is None


def test_image_wrapped_link_detected():
    html = '<a href="https://src.example.com/gallery/page"><img src="https://my.com/t.jpg"></a>'
    urls = P.extract_urls(html)
    assert urls[0].wraps_image


def test_remove_source_urls():
    html = ('<p>Nice phone.</p><p>Original image: <a href="https://original-source.com/product/a">Original</a></p>'
            '<p>Text https://original-source.com/x end</p>')
    out = P.remove_source_urls(html, ["https://original-source.com/product/a"])
    assert "original-source.com" not in out and "Nice phone." in out and "Original image" not in out
