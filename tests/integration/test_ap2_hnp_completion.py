"""HNP checkout chain against PostgreSQL: a valid chain yields one order, an altered one none."""

import hashlib
import json
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import jwt
import psycopg
import pytest
from ap2.sdk.generated.checkout_mandate import CheckoutMandate
from ap2.sdk.generated.checkout_receipt import CheckoutReceiptError
from ap2.sdk.generated.open_checkout_mandate import OpenCheckoutMandate
from ap2.sdk.mandate import MandateClient
from ap2.sdk.sdjwt import common
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient
from jwcrypto.jwk import JWK
from jwt.utils import base64url_encode

from commerce_lab.ap2_checkout import MerchantCheckoutSigner
from commerce_lab.api import ap2_signer, app
from commerce_lab.context import issue_lab_session
from commerce_lab.db import database_url, migrate, seed
from commerce_lab.settings import get_settings

pytestmark = pytest.mark.mutates_checkout_state

ISSUER = "http://127.0.0.1:4120"
OFFER_ID = "Q100348826-512GB"
APPROVED_TOKEN = "vt_" + "a" * 64


def _pem() -> bytes:
    return ec.generate_private_key(ec.SECP256R1()).private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


@pytest.fixture
def keys(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[JWK, JWK, MerchantCheckoutSigner]]:
    provider, agent = JWK.from_pem(_pem()), JWK.from_pem(_pem())
    signer = MerchantCheckoutSigner(_pem().decode(), issuer=ISSUER)
    monkeypatch.setenv("MERCHANT_AP2_REQUIRED", "true")
    monkeypatch.setenv("MERCHANT_AP2_AGENT_JWK_JSON", provider.export_public())
    get_settings.cache_clear()

    def confirm_payment(*, token: str, **_: object) -> str:
        assert token == APPROVED_TOKEN
        return "approved"

    monkeypatch.setattr("commerce_lab.checkout.confirm_payment", confirm_payment)
    app.dependency_overrides[ap2_signer] = lambda: signer
    yield provider, agent, signer
    app.dependency_overrides.clear()


def _ready_checkout(actor: str) -> tuple[TestClient, dict[str, str], str]:
    episode = seed(actor_id=actor, variant="B0")
    token = issue_lab_session(str(episode["run_id"]), actor)
    headers = {"Authorization": f"Bearer {token}", "API-Version": "2026-04-17"}
    client = TestClient(app)
    created = client.post(
        "/checkout_sessions",
        headers={**headers, "Idempotency-Key": "create"},
        json={"line_items": [{"id": OFFER_ID}], "currency": "cop", "capabilities": {}},
    )
    assert created.status_code == 201, created.text
    checkout_id = created.json()["id"]
    updated = client.post(
        f"/checkout_sessions/{checkout_id}",
        headers={**headers, "Idempotency-Key": "ready"},
        json={
            "buyer": {"email": "hnp@example.test", "first_name": "Hnp", "last_name": "Buyer"},
            "fulfillment_details": {
                "name": "Hnp Buyer",
                "email": "hnp@example.test",
                "phone_number": "+573000000000",
                "address": {
                    "name": "Hnp Buyer",
                    "line_one": "Calle 100 # 10-20",
                    "city": "Bogota",
                    "state": "DC",
                    "country": "CO",
                    "postal_code": "110111",
                },
            },
            "selected_fulfillment_options": [
                {
                    "type": "shipping",
                    "option_id": f"ship_{checkout_id}",
                    "item_ids": [f"li_{checkout_id}"],
                }
            ],
        },
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["status"] == "ready_for_payment"
    return client, headers, checkout_id


def _chain(provider: JWK, agent: JWK, checkout_jwt: str, *, variant: str, title: str) -> str:
    now = int(datetime.now(UTC).timestamp())
    open_mandate = OpenCheckoutMandate.model_validate(
        {
            "constraints": [
                {
                    "type": "checkout.allowed_merchants",
                    "allowed": [
                        {
                            "id": "tesis_merchant",
                            "name": "Tesis Commerce Merchant",
                            "website": ISSUER,
                        }
                    ],
                },
                {
                    "type": "checkout.line_items",
                    "items": [
                        {
                            "id": "line_1",
                            "quantity": 1,
                            "acceptable_items": [{"id": variant, "title": title}],
                        }
                    ],
                },
            ],
            "cnf": {"jwk": json.loads(agent.export_public())},
            "iat": now - 60,
            "exp": now + 3_600,
        }
    )
    digest = base64url_encode(hashlib.sha256(checkout_jwt.encode()).digest()).decode()
    closed = CheckoutMandate(
        checkout_jwt=checkout_jwt, checkout_hash=digest, iat=now, exp=now + 300
    )
    client = MandateClient()
    return client.present(
        agent, client.create([open_mandate], provider), [closed], aud=ISSUER, nonce=digest
    )


def _complete(client: TestClient, headers: dict[str, str], checkout_id: str, chain: str) -> Any:
    return client.post(
        f"/checkout_sessions/{checkout_id}/ap2/complete",
        headers={**headers, "Idempotency-Key": "op_hnp:complete"},
        json={
            "checkout_mandate": chain,
            "payment_data": {
                "handler_id": "tesis_sandbox",
                "instrument": {
                    "type": "sandbox_token",
                    "credential": {"type": "vault_token", "token": APPROVED_TOKEN},
                },
            },
        },
    )


def _orders(checkout_id: str) -> int:
    with psycopg.connect(database_url()) as connection:
        row = connection.execute(
            "SELECT count(*) FROM orders WHERE checkout_id = %s", (checkout_id,)
        ).fetchone()
    assert row is not None
    return row[0]


def test_valid_hnp_chain_completes_one_order_and_replays_it(
    keys: tuple[JWK, JWK, MerchantCheckoutSigner],
) -> None:
    provider, agent, signer = keys
    migrate()
    client, headers, checkout_id = _ready_checkout("hnp-chain-valid")
    signed = client.get(f"/checkout_sessions/{checkout_id}/ap2/checkout-jwt", headers=headers)
    assert signed.status_code == 200, signed.text
    title = client.get(f"/checkout_sessions/{checkout_id}", headers=headers).json()["line_items"][
        0
    ]["item"]["name"]
    chain = _chain(provider, agent, signed.json()["checkout_jwt"], variant=OFFER_ID, title=title)

    first = _complete(client, headers, checkout_id, chain)
    assert first.status_code == 200, first.text
    order_id = first.json()["checkout"]["order"]["id"]
    receipt = jwt.decode(
        first.json()["checkout_receipt_jwt"],
        jwt.PyJWK.from_dict(signer.jwk).key,
        algorithms=["ES256"],
        issuer=ISSUER,
        options={"verify_aud": False},
    )
    reference = common.compute_sd_hash(common.parse_token(chain.split("~~")[1]))
    assert (receipt["status"], receipt["order_id"], receipt["reference"]) == (
        "Success",
        order_id,
        reference,
    )
    replay = _complete(client, headers, checkout_id, chain)
    assert replay.status_code == 200, replay.text
    assert replay.json()["checkout"]["order"]["id"] == order_id
    assert _orders(checkout_id) == 1
    stored = client.get(f"/checkout_sessions/{checkout_id}", headers=headers).json()
    assert stored["status"] == "completed"


def test_altered_hnp_chain_leaves_the_checkout_untouched(
    keys: tuple[JWK, JWK, MerchantCheckoutSigner],
) -> None:
    provider, agent, signer = keys
    migrate()
    client, headers, checkout_id = _ready_checkout("hnp-chain-altered")
    signed = client.get(f"/checkout_sessions/{checkout_id}/ap2/checkout-jwt", headers=headers)
    assert signed.status_code == 200, signed.text
    chain = _chain(
        provider, agent, signed.json()["checkout_jwt"], variant="Q000-OTHER", title="Other"
    )

    rejected = _complete(client, headers, checkout_id, chain)
    assert rejected.status_code == 422, rejected.text
    assert rejected.json()["code"] == "invalid_mandate"
    receipt = CheckoutReceiptError.model_validate(
        jwt.decode(
            rejected.json()["checkout_receipt_jwt"],
            jwt.PyJWK.from_dict(signer.jwk).key,
            algorithms=["ES256"],
            issuer=ISSUER,
            options={"verify_aud": False},
        )
    )
    assert receipt.error == "invalid_mandate"
    assert _orders(checkout_id) == 0
    stored = client.get(f"/checkout_sessions/{checkout_id}", headers=headers).json()
    assert stored["status"] == "ready_for_payment"
