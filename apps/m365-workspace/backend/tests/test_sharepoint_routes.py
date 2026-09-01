"""SharePoint routes through the full app, against the mock Graph."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.graph_mock import FILE, FOLDER, SITE, last

BASE = "/api/v1/sharepoint"


def test_sites_and_site(api: TestClient) -> None:
    res = api.get(f"{BASE}/sites", params={"q": "intranet"})
    assert res.status_code == 200
    assert res.json()[0]["display_name"] == "Intranet"

    res = api.get(f"{BASE}/sites/{SITE['id']}")
    assert res.status_code == 200
    assert res.json()["id"] == SITE["id"]


def test_drives_lists_and_list_items(api: TestClient) -> None:
    res = api.get(f"{BASE}/sites/{SITE['id']}/drives")
    assert res.status_code == 200
    assert res.json()[0]["id"] == "drive-1"

    res = api.get(f"{BASE}/sites/{SITE['id']}/lists")
    assert res.status_code == 200
    assert res.json()[0]["template"] == "genericList"

    res = api.get(f"{BASE}/sites/{SITE['id']}/lists/list-1/items")
    assert res.status_code == 200
    assert res.json()[0]["fields"] == {"Title": "Do thing", "Status": "Open"}


def test_items_root_path_and_by_path(api: TestClient) -> None:
    res = api.get(f"{BASE}/drives/drive-1/items")
    assert res.status_code == 200
    assert [i["name"] for i in res.json()] == ["notes.md", "Reports"]
    assert res.json()[0]["parent_id"] == "01F!root"

    res = api.get(f"{BASE}/drives/drive-1/items", params={"path": "Reports"})
    assert res.status_code == 200
    assert len(res.json()) == 1

    res = api.get(f"{BASE}/drives/drive-1/item-by-path", params={"path": "Reports"})
    assert res.status_code == 200
    assert res.json()["is_folder"] is True

    res = api.get(f"{BASE}/drives/drive-1/items", params={"path": "a", "item_id": "b"})
    assert res.status_code == 400


def test_search(api: TestClient) -> None:
    res = api.get(f"{BASE}/drives/drive-1/search", params={"q": "budget"})
    assert res.status_code == 200
    assert res.json()[0]["name"] == "notes.md"
    assert "search(q='budget')" in last("GET", "/drives/drive-1/search")["path"]


def test_download_is_typed_from_metadata(api: TestClient) -> None:
    res = api.get(f"{BASE}/drives/drive-1/items/{FILE['id']}/content")
    assert res.status_code == 200
    assert res.content == b"# Title\nhello world"
    assert res.headers["content-type"].startswith("text/markdown")
    assert 'filename="notes.md"' in res.headers["content-disposition"]


def test_unknown_item_is_a_graph_404(api: TestClient) -> None:
    res = api.get(f"{BASE}/drives/drive-1/items/nope")
    assert res.status_code == 404
    assert res.json()["code"] == "graph_not_found"


def test_upload_to_root_with_rename(api: TestClient) -> None:
    res = api.post(
        f"{BASE}/drives/drive-1/upload",
        files={"file": ("summary.md", b"# Q3", "text/markdown")},
        data={"conflict": "rename"},
    )
    assert res.status_code == 201
    assert res.json()["name"] == "summary.md"
    put = last("PUT")
    assert put["path"].endswith("/drives/drive-1/items/root:/summary.md:/content")
    assert put["query"]["@microsoft.graph.conflictBehavior"] == "rename"
    assert put["raw"] == b"# Q3"


def test_upload_into_a_path(api: TestClient) -> None:
    res = api.post(
        f"{BASE}/drives/drive-1/upload",
        files={"file": ("a.md", b"x", "text/markdown")},
        data={"parent_path": "Reports"},
    )
    assert res.status_code == 201
    assert last("PUT")["path"].endswith("/drives/drive-1/root:/Reports/a.md:/content")


def test_create_folder_by_path_resolves_the_parent(api: TestClient) -> None:
    res = api.post(
        f"{BASE}/drives/drive-1/folders", json={"name": "Q4", "parent_path": "Reports"}
    )
    assert res.status_code == 201
    assert res.json()["is_folder"] is True
    posted = last("POST", "/children")
    assert posted["path"].endswith(f"/items/{FOLDER['id']}/children")
    assert posted["body"]["name"] == "Q4"
    assert posted["body"]["@microsoft.graph.conflictBehavior"] == "fail"


def test_folder_name_with_a_slash_is_a_400(api: TestClient) -> None:
    res = api.post(f"{BASE}/drives/drive-1/folders", json={"name": "a/b"})
    assert res.status_code == 400
    assert res.json()["code"] == "bad_request"


def test_move_rename_conflict_and_delete(api: TestClient) -> None:
    res = api.patch(
        f"{BASE}/drives/drive-1/items/{FILE['id']}",
        json={"new_parent_id": FOLDER["id"], "new_name": "renamed.md"},
    )
    assert res.status_code == 200
    assert res.json()["name"] == "renamed.md"
    patched = last("PATCH", "/drives/")["body"]
    assert patched["parentReference"] == {"id": FOLDER["id"]}

    res = api.patch(f"{BASE}/drives/drive-1/items/{FILE['id']}", json={"new_name": "taken.md"})
    assert res.status_code == 409
    assert res.json()["code"] == "m365_error"

    res = api.patch(f"{BASE}/drives/drive-1/items/{FILE['id']}", json={})
    assert res.status_code == 400

    res = api.delete(f"{BASE}/drives/drive-1/items/{FILE['id']}")
    assert res.status_code == 204
    assert last("DELETE", "/drives/") is not None
