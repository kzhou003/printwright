from fastapi.testclient import TestClient

from printwright.server import app

H = {"X-Printwright": "1"}


def client(host="127.0.0.1"):
    return TestClient(app, base_url=f"http://{host}")


def test_rejects_foreign_host_and_missing_header():
    assert client("evil.example").get("/api/projects").status_code == 403
    assert client().post("/api/projects", json={"title": "x"}).status_code == 403


def test_project_lifecycle():
    c = client()
    p = c.post("/api/projects", json={"title": "Gear", "mode": "precision"}, headers=H).json()
    pid = p["id"]
    g = c.get(f"/api/projects/{pid}").json()
    assert g["spec"]["mode"] == "precision" and g["backend"] == "cadquery"
    assert not g["can_freeze"]  # precision needs a critical dimension
    r = c.post(f"/api/projects/{pid}/freeze", json={"frozen": True}, headers=H)
    assert r.status_code == 400
    r = c.post(f"/api/projects/{pid}/mode", json={"mode": "creative"}, headers=H).json()
    assert r["spec"]["mode"] == "creative"
    spec = r["spec"]
    spec["dimensions"] = [{"name": "height", "value": 50}]
    r = c.put(f"/api/projects/{pid}/spec", json=spec, headers=H).json()
    assert r["can_freeze"]
    assert (
        c.post(f"/api/projects/{pid}/freeze", json={"frozen": True}, headers=H).json()["spec"]["status"]
        == "frozen"
    )
    assert any(x["id"] == pid for x in c.get("/api/projects").json())


def test_path_traversal_blocked():
    c = client()
    pid = c.post("/api/projects", json={"title": "x"}, headers=H).json()["id"]
    assert c.get(f"/api/projects/{pid}/files/../../settings.json").status_code == 404
    assert c.get("/api/projects/..%2F..%2Fetc/files/passwd").status_code == 404


def test_invalid_spec_is_422():
    c = client()
    pid = c.post("/api/projects", json={"title": "x"}, headers=H).json()["id"]
    assert c.put(f"/api/projects/{pid}/spec", json={"mode": "wild"}, headers=H).status_code == 422


def test_static_app_served():
    c = client()
    assert c.get("/").status_code == 200
    assert "Viewer" in c.get("/app.js").text
