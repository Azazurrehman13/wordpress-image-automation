from app.services.image_extraction_service import parse_html, upgrade_url

HTML = """
<html><head>
<meta property="og:image" content="https://src.com/img/og-front.jpg">
<script type="application/ld+json">{"@graph":[
 {"@type":"Product","url":"https://src.com/product/x","image":["https://src.com/img/ld-back.jpg"]},
 {"@type":"Product","url":"https://src.com/product/other","image":["https://src.com/img/OTHER-ld.jpg"]}]}</script>
</head><body>
<header><img src="/img/header-photo.jpg"></header>
<div class="woocommerce-product-gallery">
  <img src="https://src.com/wp-content/uploads/phone-300x300.jpg" data-large_image="https://src.com/wp-content/uploads/phone-side.jpg">
  <img src="/images/logo.png"><img srcset="/g-400.jpg 400w, /g-1200.jpg 1200w" src="/g-400.jpg">
  <a href="/full/big-view.jpg"><img src="/thumb.jpg"></a>
</div>
<section class="related products"><ul class="products"><li class="product"><img src="/img/RELATED-1.jpg"></li></ul></section>
<div class="you-may-also-like"><img src="/img/ALSO-LIKE.jpg"></div>
<img src="/img/random-banner-photo.jpg"><img data-src="https://cdn.src.com/lazy-OTHER.webp">
<script>var x="https://src.com/img/SCRIPT-OTHER.jpg";</script>
<footer><img src="/img/footer-photo.jpg"></footer>
</body></html>
"""


def urls():
    return [c.url for c in parse_html(HTML, "https://src.com/product/x")]


def test_only_this_products_images_are_collected():
    u = urls()
    for want in ("https://src.com/img/og-front.jpg", "https://src.com/img/ld-back.jpg",
                 "https://src.com/wp-content/uploads/phone-side.jpg", "https://src.com/g-1200.jpg", "https://src.com/full/big-view.jpg"):
        assert want in u
    assert not any(bad in x for x in u for bad in ("logo", "RELATED", "ALSO-LIKE", "OTHER", "header", "footer", "random-banner", "SCRIPT"))


def test_gallery_images_rank_first_and_thumbnails_upgraded():
    cands = parse_html(HTML, "https://src.com/product/x")
    assert cands[0].origin.startswith("gallery")
    assert upgrade_url("https://x.com/a-300x300.jpg") == "https://x.com/a.jpg"
    assert upgrade_url("https://cdn.shopify.com/p_600x.jpg") == "https://cdn.shopify.com/p.jpg"


def test_without_gallery_uses_product_block_and_skips_similar_items():
    html = """<html><body><nav><img src="/nav.jpg"></nav><main>
      <div itemscope itemtype="https://schema.org/Product"><img src="/p/1.jpg"><img src="/p/2.jpg"></div>
      <div class="similar-items"><img src="/OTHER.jpg"></div></main></body></html>"""
    u = [c.url for c in parse_html(html, "https://src.com/product/x")]
    assert u == ["https://src.com/p/1.jpg", "https://src.com/p/2.jpg"]


def test_background_image_in_gallery_is_found():
    html = '<div class="product-gallery"><div style="background-image:url(\'/img/bg.jpg\')"></div></div><div class="related"><img src="/R.jpg"></div>'
    assert [c.url for c in parse_html(html, "https://src.com/p")] == ["https://src.com/img/bg.jpg"]
