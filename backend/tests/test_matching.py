from app.services.product_search_service import ProductSearchService
from app.utils.text import match_score


def test_exact_match_is_100():
    assert match_score("Samsung Galaxy A15", "samsung galaxy a15") == 100


def test_extra_words_score_lower_than_exact():
    assert match_score("Samsung Galaxy A15", "Samsung Galaxy A15 128GB") < 100
    assert match_score("Samsung Galaxy A15", "Samsung Galaxy A15 128GB") > match_score("Samsung Galaxy A15", "Samsung Galaxy S24")


def test_unrelated_is_low():
    assert match_score("Samsung Galaxy A15", "Kitchen Blender 500W") < 40


def test_auto_select_requires_clear_winner():
    m = [{"id": 1, "score": 95}, {"id": 2, "score": 48}]
    assert ProductSearchService.auto_select(m, 85)["id"] == 1
    assert ProductSearchService.auto_select([{"id": 1, "score": 90}, {"id": 2, "score": 88}], 85) is None
    assert ProductSearchService.auto_select([{"id": 1, "score": 60}], 85) is None
    assert ProductSearchService.auto_select([], 85) is None
