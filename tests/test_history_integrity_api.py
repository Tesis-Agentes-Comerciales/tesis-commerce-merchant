"""Corrupt history is a non-retryable 500; storage failures remain a retryable 503."""

import logging
from collections.abc import Callable
from typing import Any, NamedTuple

import psycopg
import pytest
from fastapi.testclient import TestClient

from commerce_lab.api import app, trusted_context
from commerce_lab.contracts import ExecutionContext
from commerce_lab.contracts.creation_lookup import LOOKUP_SCHEMA, CreationLookupError
from commerce_lab.contracts.guarded_creation import GuardedError, offer_snapshot_sha256
from commerce_lab.creation_lookup import CheckoutCreationLookup, LookupIntegrityError
from commerce_lab.creation_lookup_api import LOOKUP_PATH
from commerce_lab.fixtures import fresh_p0_offers
from commerce_lab.guarded_creation import GuardedCreation, GuardIntegrityError
from commerce_lab.guarded_creation_api import GUARDED_PATH

SECRET = "do-not-reflect-synthetic-detail"
HEADERS = {"API-Version": "2026-04-17", "Idempotency-Key": "integrity-key-do-not-reflect"}


class Route(NamedTuple):
    service: type
    method: str
    path: str
    body: Callable[[], dict[str, Any]]
    envelope: type[GuardedError] | type[CreationLookupError]
    integrity_error: type[Exception]
    unavailable_code: str
    logger: str


def guarded_body() -> dict[str, Any]:
    offer = fresh_p0_offers()[0]
    return {
        "schema_version": "merchant-guarded-acp-create/1",
        "acp_request": {"line_items": [{"id": offer.id}], "currency": "cop", "capabilities": {}},
        "offer_id": offer.id,
        "expected_offer_revision": offer.revision,
        "offer_snapshot_profile": "merchant-offer-snapshot/1",
        "offer_snapshot_sha256": offer_snapshot_sha256(offer),
    }


ROUTES = {
    "guarded_creation": Route(
        GuardedCreation,
        "create",
        GUARDED_PATH,
        guarded_body,
        GuardedError,
        GuardIntegrityError,
        "guard_unavailable",
        "commerce_lab.guarded_creation_api",
    ),
    "creation_lookup": Route(
        CheckoutCreationLookup,
        "lookup",
        LOOKUP_PATH,
        lambda: {"schema_version": LOOKUP_SCHEMA, "idempotency_key_sha256": "1" * 64},
        CreationLookupError,
        LookupIntegrityError,
        "lookup_unavailable",
        "commerce_lab.creation_lookup_api",
    ),
}


@pytest.fixture
def trusted():
    app.dependency_overrides[trusted_context] = lambda: ExecutionContext(
        actor_id="integrity-owner",
        run_id="00000000-0000-0000-0000-000000000001",
        request_id="00000000-0000-0000-0000-000000000002",
        received_at="2026-09-16T12:00:00Z",
    )
    yield
    app.dependency_overrides.clear()


def post_failing(monkeypatch, route: Route, error: Exception):
    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(route.service, route.method, fail)
    return TestClient(app).post(route.path, headers=HEADERS, json=route.body())


@pytest.mark.parametrize("name", sorted(ROUTES))
def test_integrity_failure_is_logged_non_retryable_500(trusted, monkeypatch, caplog, name):
    route = ROUTES[name]
    with caplog.at_level(logging.ERROR, logger=route.logger):
        response = post_failing(monkeypatch, route, route.integrity_error(SECRET))
    assert response.status_code == 500
    assert response.headers["Cache-Control"] == "no-store"
    error = route.envelope.model_validate(response.json())
    assert error.code == "history_invalid"
    assert error.request_id == response.headers["Request-Id"]
    assert SECRET not in response.text and HEADERS["Idempotency-Key"] not in response.text
    [record] = [r for r in caplog.records if r.name == route.logger]
    assert record.levelno == logging.ERROR and record.exc_info is not None
    assert record.__dict__["request_id"] == response.headers["Request-Id"]


@pytest.mark.parametrize("name", sorted(ROUTES))
def test_storage_failure_remains_retryable_503(trusted, monkeypatch, caplog, name):
    route = ROUTES[name]
    with caplog.at_level(logging.WARNING, logger=route.logger):
        response = post_failing(monkeypatch, route, psycopg.OperationalError(SECRET))
    assert response.status_code == 503
    assert response.headers["Cache-Control"] == "no-store"
    assert route.envelope.model_validate(response.json()).code == route.unavailable_code
    assert SECRET not in response.text
    [record] = [r for r in caplog.records if r.name == route.logger]
    assert record.levelno == logging.WARNING and SECRET not in record.getMessage()
    assert record.__dict__["request_id"] == response.headers["Request-Id"]
