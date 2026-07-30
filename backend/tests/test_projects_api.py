import pytest


@pytest.mark.asyncio
async def test_create_project(logged_in_client) -> None:
    response = await logged_in_client.post("/api/projects", json={"name": "demo"})
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "demo"
    assert body["file_count"] == 0


@pytest.mark.asyncio
async def test_upload_files_builds_tree_and_repo_map(logged_in_client) -> None:
    create = await logged_in_client.post("/api/projects", json={"name": "demo"})
    project_id = create.json()["id"]

    upload = await logged_in_client.post(
        f"/api/projects/{project_id}/upload",
        files=[
            ("files", ("src/main.py", b"print('hello')\n", "text/plain")),
            ("files", ("README.md", b"# demo\n", "text/plain")),
            ("files", ("assets/logo.png", b"\x89PNG\x00binary", "application/octet-stream")),
        ],
    )
    assert upload.status_code == 200
    body = upload.json()
    assert body["ingested_count"] == 2
    assert body["skipped_binary"] == ["assets/logo.png"]
    assert body["project"]["file_count"] == 2

    tree = await logged_in_client.get(f"/api/projects/{project_id}/tree")
    assert tree.status_code == 200
    root = tree.json()
    names = {c["name"] for c in root["children"]}
    assert names == {"src", "README.md"}

    repo_map = await logged_in_client.get(f"/api/projects/{project_id}/repo-map")
    assert repo_map.status_code == 200
    assert "2 files" in repo_map.json()["text"]
    assert "main.py" in repo_map.json()["text"]


@pytest.mark.asyncio
async def test_upload_rejects_path_traversal(logged_in_client) -> None:
    create = await logged_in_client.post("/api/projects", json={"name": "demo"})
    project_id = create.json()["id"]

    upload = await logged_in_client.post(
        f"/api/projects/{project_id}/upload",
        files=[("files", ("../../etc/passwd", b"pwned", "text/plain"))],
    )
    assert upload.status_code == 400


@pytest.mark.asyncio
async def test_pin_file_persists(logged_in_client) -> None:
    create = await logged_in_client.post("/api/projects", json={"name": "demo"})
    project_id = create.json()["id"]
    await logged_in_client.post(
        f"/api/projects/{project_id}/upload",
        files=[("files", ("main.py", b"print(1)\n", "text/plain"))],
    )

    tree = (await logged_in_client.get(f"/api/projects/{project_id}/tree")).json()
    file_id = tree["children"][0]["file_id"]

    pin = await logged_in_client.patch(f"/api/projects/{project_id}/files/{file_id}", json={"pinned": True})
    assert pin.status_code == 200

    tree_after = (await logged_in_client.get(f"/api/projects/{project_id}/tree")).json()
    assert tree_after["children"][0]["pinned"] is True


@pytest.mark.asyncio
async def test_get_file_content(logged_in_client) -> None:
    create = await logged_in_client.post("/api/projects", json={"name": "demo"})
    project_id = create.json()["id"]
    await logged_in_client.post(
        f"/api/projects/{project_id}/upload",
        files=[("files", ("src/main.py", b"print('hi')\n", "text/plain"))],
    )

    tree = (await logged_in_client.get(f"/api/projects/{project_id}/tree")).json()
    file_id = tree["children"][0]["children"][0]["file_id"]

    content = await logged_in_client.get(f"/api/projects/{project_id}/files/{file_id}/content")
    assert content.status_code == 200
    assert content.json() == {"path": "src/main.py", "content": "print('hi')\n"}


@pytest.mark.asyncio
async def test_snippet_upload_adds_a_file(logged_in_client) -> None:
    create = await logged_in_client.post("/api/projects", json={"name": "demo"})
    project_id = create.json()["id"]

    resp = await logged_in_client.post(
        f"/api/projects/{project_id}/snippet", json={"path": "notes.txt", "content": "hello world"}
    )
    assert resp.status_code == 200
    assert resp.json()["project"]["file_count"] == 1


@pytest.mark.asyncio
async def test_delete_project_removes_it(logged_in_client) -> None:
    create = await logged_in_client.post("/api/projects", json={"name": "demo"})
    project_id = create.json()["id"]

    delete = await logged_in_client.delete(f"/api/projects/{project_id}")
    assert delete.status_code == 200

    get_after = await logged_in_client.get(f"/api/projects/{project_id}")
    assert get_after.status_code == 404


@pytest.mark.asyncio
async def test_other_user_cannot_access_project(api_client, test_db_engine) -> None:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from app.core.security import hash_password
    from app.models.user import User

    session_maker = async_sessionmaker(test_db_engine, expire_on_commit=False, class_=AsyncSession)
    async with session_maker() as session:
        session.add_all(
            [
                User(username="owner", password_hash=hash_password("password123"), role="user"),
                User(username="intruder", password_hash=hash_password("password123"), role="user"),
            ]
        )
        await session.commit()

    await api_client.post("/api/auth/login", json={"username": "owner", "password": "password123"})
    create = await api_client.post("/api/projects", json={"name": "demo"})
    project_id = create.json()["id"]

    await api_client.post("/api/auth/login", json={"username": "intruder", "password": "password123"})
    response = await api_client.get(f"/api/projects/{project_id}")
    assert response.status_code == 403
