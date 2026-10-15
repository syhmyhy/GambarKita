from tests.conftest import auth


def test_create_upload_list_delete_roundtrip(client, create_event, upload_photo):
    created = create_event("Smoke")
    event_id = created["event"]["id"]

    response = upload_photo(event_id)
    assert response.status_code == 201, response.text
    photo = response.json()

    listing = client.get(f"/api/events/{event_id}/photos").json()
    assert [item["id"] for item in listing["items"]] == [photo["id"]]

    assert client.get(photo["url"]).status_code == 200
    deleted = client.delete(f"/api/events/{event_id}/photos/{photo['id']}", headers=auth(created["host_token"]))
    assert deleted.status_code == 204
    assert client.get(photo["url"]).status_code == 404
