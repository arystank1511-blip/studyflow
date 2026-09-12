"""Staging-only RLS check using two real, distinct signed-in test accounts.

Set SUPABASE_URL, SUPABASE_PUBLISHABLE_KEY, TEST_ACCESS_TOKEN_A/B privately.
Creates synthetic tasks and deletes only their returned IDs in finally.
Never use real users' tokens. No keys/tokens/response bodies are printed.
"""
import os
from datetime import date
from uuid import uuid4

import httpx
from dotenv import load_dotenv

from cloud import valid_config


def main():
    load_dotenv()
    url, key = os.environ["SUPABASE_URL"].rstrip("/"), os.environ["SUPABASE_PUBLISHABLE_KEY"]
    if not valid_config(url, key):
        raise RuntimeError("Use a valid project URL and publishable key")
    tokens = [os.environ["TEST_ACCESS_TOKEN_A"], os.environ["TEST_ACCESS_TOKEN_B"]]
    clients = [httpx.Client(base_url=url, headers={"apikey": key, "Authorization": "Bearer " + token}, timeout=15) for token in tokens]
    created = []
    try:
        users = [client.get("/auth/v1/user") for client in clients]
        assert all(response.status_code == 200 for response in users), "Invalid test sessions"
        ids = [response.json()["id"] for response in users]
        assert ids[0] != ids[1], "Two different accounts are required"
        path = "/rest/v1/studyflow_tasks"
        for client in clients:
            response = client.post(path, headers={"Prefer": "return=representation"}, json={
                "title": "Disposable RLS test " + uuid4().hex, "course": "Security test", "due_date": date.today().isoformat()})
            assert response.status_code == 201, "Could not create own task"
            created.append((client, response.json()[0]["id"]))
        for index, client in enumerate(clients):
            own, other = created[index][1], created[1 - index][1]
            assert len(client.get(path, params={"id": "eq." + str(own)}).json()) == 1, "Own task not readable"
            query = {"id": "eq." + str(other)}
            for method, data in (("GET", None), ("PATCH", {"title": "Should not change"}), ("DELETE", None)):
                response = client.request(method, path, params=query, json=data, headers={"Prefer": "return=representation"})
                assert response.status_code in (200, 204, 401, 403), "Unexpected RLS response"
                assert response.status_code in (204, 401, 403) or response.json() == [], "Foreign data exposed or modified"
            response = client.post(path, json={"title": "Forbidden", "course": "Security test", "due_date": date.today().isoformat(), "user_id": ids[1 - index]})
            assert response.status_code in (401, 403), "Foreign ownership accepted"
        anonymous = httpx.get(url + path, headers={"apikey": key}, timeout=15)
        assert anonymous.status_code in (401, 403), "Anonymous table access must be denied"
        for client, task_id in created:
            response = client.get(path, params={"id": "eq." + str(task_id)})
            assert response.status_code == 200 and response.json()[0]["title"].startswith("Disposable RLS test "), "Foreign write succeeded"
        print("PASS: own CRUD, cross-account read/write/delete/ownership and anonymous denial")
    finally:
        cleanup_failed = False
        for client, task_id in created:
            try:
                cleanup_failed |= client.delete("/rest/v1/studyflow_tasks", params={"id": "eq." + str(task_id)}).status_code not in (200, 204)
            except httpx.HTTPError:
                cleanup_failed = True
        for client in clients:
            client.close()
        if cleanup_failed:
            print("WARNING: cleanup incomplete; remove only the Disposable RLS test tasks in staging.")


if __name__ == "__main__":
    main()
