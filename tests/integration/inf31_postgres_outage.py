"""Explicit CI probe. Stops only the disposable PostgreSQL service of its job."""

import os
import socket
import subprocess
import threading
from collections.abc import Iterator
from time import monotonic, sleep
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx
import psycopg
import pytest
import uvicorn
from psycopg import sql

from commerce_lab.api import app
from commerce_lab.context import issue_lab_session
from commerce_lab.db import database_url, seed

TABLES = (
    "checkout_sessions",
    "checkout_mutations",
    "checkout_completion_attempts",
    "guarded_checkout_creations",
    "rejected_guarded_checkout_attempts",
    "orders",
    "webhook_deliveries",
    "webhook_delivery_attempts",
    "run_events",
    "catalog_offers",
    "lab_sessions",
    "request_observations",
)


def _docker(*args: str) -> str:
    return subprocess.check_output(["docker", *args], text=True, timeout=35).strip()


def _snapshot(cluster: str) -> dict[str, str]:
    with psycopg.connect(database_url(), connect_timeout=2) as connection:
        cluster_row = connection.execute("SHOW data_directory").fetchone()
        assert cluster_row == (cluster,)
        rows: dict[str, str] = {}
        for table in TABLES:
            query = sql.SQL(
                "SELECT md5(COALESCE(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t))::text, '[]'))"
                " FROM {} AS t"
            ).format(sql.Identifier(table))
            result = connection.execute(query).fetchone()
            assert result is not None
            rows[table] = result[0]
        return rows


@pytest.fixture
def live_http() -> Iterator[httpx.Client]:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        host, port = listener.getsockname()
        server = uvicorn.Server(uvicorn.Config(app, log_config=None))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        thread.start()
        try:
            deadline = monotonic() + 10
            while not server.started and thread.is_alive() and monotonic() < deadline:
                sleep(0.05)
            assert server.started
            with httpx.Client(
                base_url=f"http://{host}:{port}", timeout=10, trust_env=False
            ) as client:
                yield client
        finally:
            server.should_exit = True
            thread.join(15)
            assert not thread.is_alive()


def test_inf31_checkout_when_postgres_is_stopped(
    live_http: httpx.Client, caplog: pytest.LogCaptureFixture
) -> None:
    assert os.environ["RUN_DB_INTEGRATION"] == "1"
    container = os.environ["INF31_POSTGRES_CONTAINER"]
    cluster = os.environ["RECOVERY_TEST_CLUSTER"]
    address = urlsplit(database_url())
    assert address.hostname == "127.0.0.1" and address.port is not None
    assert any(
        binding.endswith(f":{address.port}")
        for binding in _docker("port", container, "5432/tcp").splitlines()
    )
    assert _docker("inspect", "-f", "{{.State.Running}}", container) == "true"
    assert live_http.get("/health/ready").status_code == 200
    actor = "inf31-" + uuid4().hex
    run_id = str(seed(actor_id=actor, variant="B0")["run_id"])
    revoked = issue_lab_session(run_id, actor)
    with psycopg.connect(database_url()) as connection:
        assert (
            connection.execute(
                "UPDATE lab_sessions SET revoked_at=now() WHERE run_id=%s", (run_id,)
            ).rowcount
            == 1
        )
        offer = connection.execute(
            "SELECT offer_id FROM catalog_offers WHERE run_id=%s AND "
            "snapshot->>'availability'='in_stock' ORDER BY offer_id LIMIT 1",
            (run_id,),
        ).fetchone()
        assert offer is not None
    token = issue_lab_session(run_id, actor)
    headers = {"API-Version": "2026-04-17", "Idempotency-Key": "inf31-outage"}
    body = {
        "line_items": [{"id": offer[0]}],
        "currency": "cop",
        "capabilities": {"payment": {"handlers": []}},
    }
    for bearer in (None, "unknown-synthetic-session", revoked):
        auth = {"Authorization": "Bearer " + bearer} if bearer is not None else {}
        denied = live_http.post("/checkout_sessions", headers=headers | auth, json=body)
        assert denied.status_code == 401
        assert denied.json()["detail"] == "A valid synthetic lab session is required."
        UUID(denied.headers["Request-Id"])
    assert (
        live_http.get(
            "/checkout_sessions/inf31-absent",
            headers=headers | {"Authorization": "Bearer " + token},
        ).status_code
        == 404
    )

    before = _snapshot(cluster)
    try:
        _docker("stop", container)
        assert _docker("inspect", "-f", "{{.State.Running}}", container) == "false"
        assert (
            live_http.post("/lab/catalog/search", json={"category": "smartphones"}).status_code
            == 401
        )
        response = live_http.post(
            "/checkout_sessions",
            headers=headers
            | {
                "Authorization": "Bearer " + token,
                "Request-Id": "untrusted",
            },
            json=body,
        )
    finally:
        _docker("start", container)
        deadline = monotonic() + 20
        while live_http.get("/health/ready").status_code != 200:
            assert monotonic() < deadline, "PostgreSQL did not recover"
            sleep(0.25)
        assert _snapshot(cluster) == before

    assert response.status_code == 503
    request_id = response.headers["Request-Id"]
    assert UUID(request_id).version == 4
    assert response.json() == {
        "detail": {
            "code": "lab_session_store_unavailable",
            "message": "Lab session verification is temporarily unavailable.",
        }
    }
    failed_writes = [
        record
        for record in caplog.records
        if record.getMessage() == "Failed to persist checkout request observation"
        and getattr(record, "request_id", None) == request_id
    ]
    assert len(failed_writes) == 1
    assert not any(record.name == "uvicorn.error" and record.exc_info for record in caplog.records)
    print("PG ready → stopped → restored; native HTTP 503; generated Request-Id:", request_id)
    print("Missing/unknown/revoked credentials while ready and missing while stopped: HTTP 401")
    print("Commercial state and request_observations unchanged; observation write failed")
