"""`run-checkouts` reports what the merchant persisted for one run (B2 pilot amendment M3)."""

import json
import sys
from uuid import uuid4

import psycopg
import pytest

from commerce_lab.checkout import CheckoutService
from commerce_lab.context import authenticate_lab_session, issue_lab_session
from commerce_lab.contracts import Success
from commerce_lab.db import database_url, migrate, run_checkouts, seed
from commerce_lab.fixtures import FIXTURE_DELIVERY_CONTEXT

# The report covers canceled and completed checkouts, so the module mutates checkout state.
pytestmark = pytest.mark.mutates_checkout_state

APPROVED_TOKEN = "vt_" + "a" * 64


@pytest.fixture(autouse=True)
def synthetic_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    def confirm_payment(*, token: str, **_: object) -> str:
        assert token == APPROVED_TOKEN
        return "approved"

    monkeypatch.setattr("commerce_lab.checkout.confirm_payment", confirm_payment)


def _service(actor_id: str) -> tuple[str, CheckoutService]:
    run_id = str(seed(actor_id=actor_id, variant="B2")["run_id"])
    token = issue_lab_session(run_id, actor_id)
    return run_id, CheckoutService(authenticate_lab_session(token))


def _prepare(service: CheckoutService, key: str, offer_id: str) -> str:
    prepared = service.prepare(
        {
            "offer_id": offer_id,
            "expected_offer_revision": 1,
            "quantity": 1,
            "delivery_context": FIXTURE_DELIVERY_CONTEXT.model_dump(),
            "idempotency_key": key,
        }
    )
    assert isinstance(prepared, Success)
    return prepared.data.id


def _complete(service: CheckoutService, checkout_id: str) -> None:
    contact = {"name": "Buyer Report", "email": "buyer.report@example.test"}
    ready = service.update(
        {
            "checkout_id": checkout_id,
            "idempotency_key": "ready",
            "buyer": {"email": contact["email"], "first_name": "Buyer", "last_name": "Report"},
            "fulfillment_details": contact
            | {
                "phone_number": "+573000000000",
                "address": {
                    "name": contact["name"],
                    "line_one": "Calle 100 # 10-20",
                    "city": "Bogota",
                    "state": "DC",
                    "country": "CO",
                    "postal_code": "110111",
                },
            },
            "selected_fulfillment_option": {
                "type": "shipping",
                "option_id": f"ship_{checkout_id}",
                "item_ids": [f"li_{checkout_id}"],
            },
        }
    )
    assert isinstance(ready, Success)
    payment = {
        "handler_id": "tesis_sandbox",
        "instrument": {
            "type": "sandbox_token",
            "credential": {"type": "vault_token", "token": APPROVED_TOKEN},
        },
    }
    assert isinstance(service.complete(checkout_id, payment, "complete"), Success)


def test_run_without_checkouts_reports_zero() -> None:
    migrate()
    run_id, _ = _service("report-empty")

    report = run_checkouts(run_id)

    assert report == {
        "operation": "run-checkouts",
        "run_id": run_id,
        "count": 0,
        "checkouts": [],
        "order_count": 0,
    }


def test_report_lists_only_the_run_checkouts_with_status_and_orders() -> None:
    migrate()
    run_id, service = _service("report-run")
    _, other = _service("report-other-run")
    _prepare(other, "foreign", "Q100286751-256GB")
    completed = _prepare(service, "completed", "Q100348826-512GB")
    canceled = _prepare(service, "canceled", "Q100286751-256GB")
    _complete(service, completed)
    assert isinstance(
        service.cancel({"checkout_id": canceled, "idempotency_key": "cancel"}), Success
    )

    report = run_checkouts(run_id)

    assert report["count"] == 2
    assert report["order_count"] == 1
    checkouts = report["checkouts"]
    assert isinstance(checkouts, list)
    assert {row["checkout_id"]: (row["status"], row["offer_id"]) for row in checkouts} == {
        completed: ("completed", "Q100348826-512GB"),
        canceled: ("canceled", "Q100286751-256GB"),
    }
    assert [list(row) for row in checkouts] == [
        ["checkout_id", "status", "offer_id", "created_at", "updated_at"]
    ] * 2
    ordering = [(row["created_at"], row["checkout_id"]) for row in checkouts]
    assert ordering == sorted(ordering)
    assert all(row["created_at"].endswith("Z") for row in checkouts)
    assert all(row["updated_at"] >= row["created_at"] for row in checkouts)


def test_unknown_run_fails_instead_of_reporting_zero_checkouts() -> None:
    migrate()

    with pytest.raises(LookupError, match="Run does not exist"):
        run_checkouts(str(uuid4()))


def test_cli_reports_without_writing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    migrate()
    run_id, service = _service("report-cli")
    checkout_id = _prepare(service, "cli", "Q100286751-256GB")
    monkeypatch.setattr(sys, "argv", ["db", "run-checkouts", run_id])
    from commerce_lab.db import __main__ as db_main

    with psycopg.connect(database_url()) as connection:
        before = connection.execute(
            "SELECT count(*) FROM run_events WHERE run_id = %s", (run_id,)
        ).fetchone()
    db_main.main()
    with psycopg.connect(database_url()) as connection:
        after = connection.execute(
            "SELECT count(*) FROM run_events WHERE run_id = %s", (run_id,)
        ).fetchone()

    report = json.loads(capsys.readouterr().out)
    assert report["count"] == 1
    assert report["checkouts"][0]["checkout_id"] == checkout_id
    assert report["checkouts"][0]["status"] == "prepared"
    assert before == after
