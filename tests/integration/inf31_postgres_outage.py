"""Explicit CI probe. Stops only the disposable PostgreSQL service of its job."""

import os
import socket
import subprocess
import threading
from collections.abc import Iterator
from time import monotonic, sleep
from traceback import extract_tb
from urllib.parse import urlsplit
from uuid import uuid4

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
    "run_events",
    "catalog_offers",
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
    token = issue_lab_session(run_id, actor)
    with psycopg.connect(database_url()) as connection:
        offer = connection.execute(
            "SELECT offer_id FROM catalog_offers WHERE run_id=%s AND "
            "snapshot->>'availability'='in_stock' ORDER BY offer_id LIMIT 1",
            (run_id,),
        ).fetchone()
        assert offer is not None
    before = _snapshot(cluster)
    try:
        _docker("stop", container)
        assert _docker("inspect", "-f", "{{.State.Running}}", container) == "false"
        response = live_http.post(
            "/checkout_sessions",
            headers={
                "Authorization": "Bearer " + token,
                "API-Version": "2026-04-17",
                "Idempotency-Key": "inf31-outage",
                "Request-Id": "untrusted",
            },
            json={
                "line_items": [{"id": offer[0]}],
                "currency": "cop",
                "capabilities": {"payment": {"handlers": []}},
            },
        )
    finally:
        _docker("start", container)
        deadline = monotonic() + 20
        while live_http.get("/health/ready").status_code != 200:
            assert monotonic() < deadline, "PostgreSQL did not recover"
            sleep(0.25)
        assert _snapshot(cluster) == before

    assert response.status_code == 500
    assert response.text == "Internal Server Error"
    assert "Request-Id" not in response.headers
    unhandled = [
        record
        for record in caplog.records
        if record.name == "uvicorn.error" and record.exc_info is not None
    ]
    assert len(unhandled) == 1 and unhandled[0].exc_info is not None
    error = unhandled[0].exc_info[1]
    assert type(error) is psycopg.OperationalError
    assert error.sqlstate is None
    assert "Connection refused" in str(error)
    frames = [frame.name for frame in extract_tb(unhandled[0].exc_info[2])]
    assert "trusted_context" in frames and "authenticate_lab_session" in frames
    assert any(
        record.message == "Failed to persist checkout request observation"
        for record in caplog.records
    )
    print("PG ready → stopped → restored; OperationalError/SQLSTATE=None; HTTP 500/no Request-Id")
    print("Commercial state and request_observations unchanged; observation write failed")
