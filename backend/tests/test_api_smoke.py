from fastapi.testclient import TestClient

from app.main import app


def test_health_and_unconnected_errors():
    with TestClient(app) as c:
        assert c.get("/health").json() == {"ok": True}
        r = c.post("/api/products/search", json={"query": "phone"})
        assert r.status_code == 409 and r.json()["error"]["code"] == "wordpress_not_connected"
        assert c.get("/api/settings").json()["auto_publish"] is False
        assert c.put("/api/settings", json={"auto_publish": True}).json()["auto_publish"] is True
        c.put("/api/settings", json={"auto_publish": False})
        assert c.get("/api/jobs/doesnotexist").status_code == 404
        assert c.get("/api/images/999/file").status_code == 404
