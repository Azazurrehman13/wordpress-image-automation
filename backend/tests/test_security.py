import pytest

from app.utils.errors import AppError
from app.utils.security import sanitize_filename, validate_public_url


def test_sanitize_filename():
    assert sanitize_filename("../../etc/passwd") == "passwd"
    assert sanitize_filename("my file (1).jpg") == "my-file-1-.jpg" or sanitize_filename("my file (1).jpg").endswith(".jpg")
    assert "/" not in sanitize_filename("a/b\\c.jpg") and ".." not in sanitize_filename("..\\..\\x.jpg")


@pytest.mark.parametrize("url", [
    "ftp://example.com/x", "file:///etc/passwd", "http://localhost/x", "http://127.0.0.1/x",
    "http://192.168.1.5/x", "http://10.0.0.1/", "http://[::1]/", "http://user:pw@example.com/", "javascript:alert(1)",
])
async def test_blocked_urls(url):
    with pytest.raises(AppError):
        await validate_public_url(url, allow_private=False)


async def test_private_allowed_only_when_configured():
    assert await validate_public_url("http://127.0.0.1:8080/x", allow_private=True)
