"""Human Not Present checkout mandate chains (docs/ap2-profile.md, «Human Not Present»).

The chains are built here with the AP2 SDK and keys generated per test, following the profile:
the provider key signs the open mandate (``cnf`` = Agent Key) and the Agent Key closes it with a
terminal KB-SD-JWT whose ``aud`` is the merchant issuer and whose ``nonce`` is the checkout hash.
"""

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import jwt
import pytest
from ap2.sdk.generated.checkout_mandate import CheckoutMandate
from ap2.sdk.generated.checkout_receipt import CheckoutReceiptError
from ap2.sdk.generated.open_checkout_mandate import OpenCheckoutMandate
from ap2.sdk.generated.payment_mandate import PaymentMandate
from ap2.sdk.mandate import MandateClient
from ap2.sdk.sdjwt import common
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient
from jwcrypto.jwk import JWK
from jwt.utils import base64url_encode
from pydantic import BaseModel, ConfigDict, SecretStr

from commerce_lab.ap2_checkout import (
    Ap2MandateError,
    MerchantCheckoutSigner,
    project_acp_checkout,
)
from commerce_lab.api import acp_checkout, ap2_signer, app, trusted_context
from commerce_lab.contracts import ExecutionContext
from commerce_lab.fixtures import FIXTURE_NOW
from commerce_lab.settings import Settings

ISSUER = "https://merchant.example.test"
MERCHANT_ID = "tesis_merchant"
VARIANT = "Q100348826-512GB"


class _RawPayload(BaseModel):
    """Lets a test sign claims outside the SDK models (unknown constraints, prefixed claims)."""

    model_config = ConfigDict(extra="allow")


def _key() -> JWK:
    return JWK.from_pem(
        ec.generate_private_key(ec.SECP256R1()).private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )


def _hash(value: str) -> str:
    return base64url_encode(hashlib.sha256(value.encode("ascii")).digest()).decode()


def _checkout(
    *, status: str = "ready_for_payment", variant: str = VARIANT, quantity: int = 1
) -> dict[str, Any]:
    return {
        "id": "chk_hnp",
        "status": status,
        "currency": "cop",
        "line_items": [
            {
                "id": "li_chk_hnp",
                "item": {"id": variant, "name": "Pixel 9 512GB", "unit_amount": 72_000},
                "quantity": quantity,
                "totals": [{"type": "total", "display_text": "Total", "amount": 72_000}],
            }
        ],
        "totals": [
            {"type": "subtotal", "display_text": "Subtotal", "amount": 72_000},
            {"type": "fulfillment", "display_text": "Envio", "amount": 2_000},
            {"type": "total", "display_text": "Total", "amount": 74_000},
        ],
        "messages": [],
        "links": [],
    }


class Chain:
    """Keys, clock and builders for one HNP checkout chain."""

    def __init__(self) -> None:
        self.now = int(datetime.now(UTC).timestamp())
        self.provider = _key()
        self.agent = _key()
        merchant_pem = (
            ec.generate_private_key(ec.SECP256R1())
            .private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            )
            .decode()
        )
        self.signer = MerchantCheckoutSigner(
            merchant_pem, issuer=ISSUER, clock=lambda: datetime.fromtimestamp(self.now, UTC)
        )
        self.trusted = self.provider.export_public()

    def open_claims(self, **overrides: Any) -> dict[str, Any]:
        claims: dict[str, Any] = {
            "vct": "mandate.checkout.open.1",
            "constraints": [
                {
                    "type": "checkout.allowed_merchants",
                    "allowed": [
                        {"id": MERCHANT_ID, "name": "Tesis Commerce Merchant", "website": ISSUER}
                    ],
                },
                {
                    "type": "checkout.line_items",
                    "items": [
                        {
                            "id": "line_1",
                            "quantity": 1,
                            "acceptable_items": [
                                {"id": VARIANT, "title": "Pixel 9 512GB"},
                                {"id": "Q111088113-256GB", "title": "Galaxy S24 256GB"},
                            ],
                        }
                    ],
                },
            ],
            "cnf": {"jwk": json.loads(self.agent.export_public())},
            "iat": self.now - 60,
            "exp": self.now + 3_600,
        }
        claims.update(overrides)
        return claims

    def open(self, claims: dict[str, Any] | None = None, *, key: JWK | None = None) -> str:
        payload = claims if claims is not None else self.open_claims()
        # In-profile claims go through the SDK model, as the issuer (HNP-04) does; anything the
        # model would reject or silently drop is signed as is.
        try:
            model: BaseModel = OpenCheckoutMandate.model_validate(payload)
            if model.model_dump(by_alias=True, exclude_none=True) != payload:
                model = _RawPayload(**payload)
        except ValueError:
            model = _RawPayload(**payload)
        return MandateClient().create([model], key or self.provider)

    def checkout_jwt(self, checkout: dict[str, Any] | None = None, *, revision: int = 3) -> str:
        return self.signer.sign(checkout or _checkout(), revision=revision)

    def close(
        self,
        open_token: str,
        checkout_jwt: str,
        *,
        key: JWK | None = None,
        aud: str = ISSUER,
        checkout_hash: str | None = None,
        nonce: str | None = None,
        iat: int | None = None,
        exp: int | None = None,
        hash_mode: Any = "sd_hash",
    ) -> str:
        digest = checkout_hash or _hash(checkout_jwt)
        closed_iat = self.now if iat is None else iat
        mandate = CheckoutMandate(
            checkout_jwt=checkout_jwt,
            checkout_hash=digest,
            iat=closed_iat,
            exp=closed_iat + 300 if exp is None else exp,
        )
        return MandateClient().present(
            key or self.agent,
            open_token,
            [mandate],
            aud=aud,
            nonce=nonce or digest,
            hash_mode=hash_mode,
        )

    def valid(self, checkout: dict[str, Any] | None = None) -> str:
        return self.close(self.open(), self.checkout_jwt(checkout))

    def verify(
        self, token: str, checkout: dict[str, Any] | None = None, *, trusted: str | None = None
    ) -> tuple[int, str]:
        return self.signer.verify_mandate_chain(
            token,
            checkout=checkout or _checkout(),
            trusted_agent_jwk_json=trusted or self.trusted,
            merchant_id=MERCHANT_ID,
        )


def _reference(token: str) -> str:
    return common.compute_sd_hash(common.parse_token(token.rsplit("~~", 1)[-1]))


def _rejects(chain: Chain, token: str, code: str, checkout: dict[str, Any] | None = None) -> None:
    with pytest.raises(Ap2MandateError) as caught:
        chain.verify(token, checkout)
    assert caught.value.code == code, caught.value.description
    assert caught.value.reference == _reference(token)


def test_valid_chain_verifies_with_the_closed_mandate_reference() -> None:
    chain = Chain()
    token = chain.valid()
    assert token.count("~~") == 1
    assert chain.verify(token) == (3, _reference(token))


def test_projection_follows_the_profile_table() -> None:
    projected = project_acp_checkout(_checkout(), merchant_id=MERCHANT_ID)
    assert projected.model_dump(mode="json", exclude_none=True) == {
        "id": "chk_hnp",
        "merchant": {"id": MERCHANT_ID, "name": "Tesis Commerce Merchant"},
        "line_items": [
            {
                "id": "li_chk_hnp",
                "item": {"id": VARIANT, "title": "Pixel 9 512GB", "price": 72_000},
                "quantity": 1,
                "totals": [],
            }
        ],
        "status": "ready_for_complete",
        "currency": "COP",
        "totals": [
            {"type": "subtotal", "amount": 72_000},
            {"type": "fulfillment", "amount": 2_000},
            {"type": "total", "amount": 74_000},
        ],
        "links": [],
    }
    completed = project_acp_checkout(_checkout(status="completed"), merchant_id=MERCHANT_ID)
    assert completed.status.value == "completed"
    for status in ("not_ready_for_payment", "canceled", "in_progress"):
        with pytest.raises(ValueError, match="not projectable"):
            project_acp_checkout(_checkout(status=status), merchant_id=MERCHANT_ID)


def _total_changed(chain: Chain) -> tuple[str, dict[str, Any]]:
    checkout = _checkout()
    checkout["totals"][-1]["amount"] = 99_000
    return chain.valid(), checkout


def _variant_out_of_scope(chain: Chain) -> tuple[str, dict[str, Any]]:
    checkout = _checkout(variant="Q999-OTHER")
    return chain.valid(checkout), checkout


def _other_merchant(chain: Chain) -> tuple[str, None]:
    claims = chain.open_claims()
    claims["constraints"][0]["allowed"] = [
        {"id": "other_merchant", "name": "Other", "website": "https://other.example.test"}
    ]
    return chain.close(chain.open(claims), chain.checkout_jwt()), None


def _closed_with_other_key(chain: Chain) -> tuple[str, None]:
    return chain.close(chain.open(), chain.checkout_jwt(), key=_key()), None


def _foreign_sd_hash(chain: Chain) -> tuple[str, None]:
    presented = chain.open()
    other = chain.open()
    closed_over_other = chain.close(other, chain.checkout_jwt()).split("~~")[1]
    return f"{presented.removesuffix('~')}~~{closed_over_other}", None


def _checkout_hash_altered(chain: Chain) -> tuple[str, None]:
    checkout_jwt = chain.checkout_jwt()
    return chain.close(
        chain.open(), checkout_jwt, checkout_hash="A" * 43, nonce=_hash(checkout_jwt)
    ), None


def _checkout_hash_and_nonce_altered(chain: Chain) -> tuple[str, None]:
    return chain.close(chain.open(), chain.checkout_jwt(), checkout_hash="A" * 43), None


def _with_constraint(constraint: dict[str, Any]) -> Callable[[Chain], tuple[str, None]]:
    def build(chain: Chain) -> tuple[str, None]:
        claims = chain.open_claims()
        claims["constraints"].append(constraint)
        return chain.close(chain.open(claims), chain.checkout_jwt()), None

    return build


def _with_claims(**overrides: Any) -> Callable[[Chain], tuple[str, None]]:
    def build(chain: Chain) -> tuple[str, None]:
        return chain.close(chain.open(chain.open_claims(**overrides)), chain.checkout_jwt()), None

    return build


def _merchant_property(chain: Chain) -> tuple[str, None]:
    claims = chain.open_claims()
    claims["constraints"][0]["allowed"][0]["mcc"] = "5732"
    return chain.close(chain.open(claims), chain.checkout_jwt()), None


def _empty_acceptable_items(chain: Chain) -> tuple[str, None]:
    claims = chain.open_claims()
    claims["constraints"][1]["items"][0]["acceptable_items"] = []
    return chain.close(chain.open(claims), chain.checkout_jwt()), None


def _constraints(
    *indexes: int, checkout: dict[str, Any] | None = None
) -> Callable[[Chain], tuple[str, dict[str, Any] | None]]:
    """Open mandate with only (or repeated) profile constraints, by index in `open_claims`."""

    def build(chain: Chain) -> tuple[str, dict[str, Any] | None]:
        claims = chain.open_claims()
        claims["constraints"] = [claims["constraints"][index] for index in indexes]
        return chain.close(chain.open(claims), chain.checkout_jwt(checkout)), checkout

    return build


def _two_units_required(chain: Chain) -> tuple[str, None]:
    claims = chain.open_claims()
    claims["constraints"][1]["items"][0]["quantity"] = 2
    return chain.close(chain.open(claims), chain.checkout_jwt()), None


def _untitled_acceptable_item(chain: Chain) -> tuple[str, None]:
    claims = chain.open_claims()
    del claims["constraints"][1]["items"][0]["acceptable_items"][0]["title"]
    return chain.close(chain.open(claims), chain.checkout_jwt()), None


def _payment_chain(chain: Chain) -> tuple[str, None]:
    checkout_jwt = chain.checkout_jwt()
    claims = chain.open_claims(
        vct="mandate.payment.open.1",
        constraints=[{"type": "payment.amount_range", "currency": "COP", "max": 80_000}],
    )
    payment = PaymentMandate.model_validate(
        {
            "transaction_id": _hash(checkout_jwt),
            "payee": {"id": "attacker", "name": "Attacker"},
            "payment_amount": {"amount": 74_000, "currency": "COP"},
            "payment_instrument": {"id": "tesis_sandbox", "type": "sandbox_token"},
            "iat": chain.now,
            "exp": chain.now + 300,
        }
    )
    token = MandateClient().present(
        chain.agent, chain.open(claims), [payment], aud=ISSUER, nonce=_hash(checkout_jwt)
    )
    return token, None


@pytest.mark.parametrize(
    ("build", "code"),
    [
        # Alterations named in the HNP-04/HNP-M1 acceptance criteria (spec §12).
        pytest.param(_total_changed, "invalid_mandate", id="total-differs-from-signed-checkout"),
        pytest.param(
            _with_constraint({"type": "payment.amount_range", "currency": "COP", "max": 1}),
            "unresolved_constraint",
            id="total-range-is-not-a-checkout-constraint",
        ),
        pytest.param(
            _with_claims(payee={"id": "attacker", "name": "Attacker"}),
            "unresolved_constraint",
            id="payee-prefixed-in-open-mandate",
        ),
        pytest.param(_payment_chain, "invalid_credential", id="payee-payment-chain-at-merchant"),
        pytest.param(_variant_out_of_scope, "invalid_mandate", id="variant"),
        pytest.param(_other_merchant, "invalid_mandate", id="merchant"),
        pytest.param(_closed_with_other_key, "invalid_credential", id="closed-with-other-key"),
        pytest.param(_foreign_sd_hash, "invalid_credential", id="foreign-sd-hash"),
        pytest.param(_checkout_hash_altered, "invalid_mandate", id="checkout-hash"),
        pytest.param(
            _checkout_hash_and_nonce_altered, "invalid_mandate", id="checkout-hash-and-nonce"
        ),
        pytest.param(
            _with_constraint({"type": "checkout.gift_wrap", "allowed": True}),
            "unresolved_constraint",
            id="unknown-constraint",
        ),
        # Other profile rules.
        pytest.param(
            _with_constraint({"type": "payment.budget", "max": 1, "currency": "COP"}),
            "unresolved_constraint",
            id="ap2-constraint-outside-profile",
        ),
        pytest.param(_merchant_property, "unresolved_constraint", id="unknown-property"),
        pytest.param(_empty_acceptable_items, "unresolved_constraint", id="empty-acceptable-items"),
        pytest.param(
            _untitled_acceptable_item, "unresolved_constraint", id="acceptable-item-without-title"
        ),
        # Each profile checkout constraint exactly once: the merchant alone bounds merchant and
        # variant (the PSP does not evaluate checkout.*).
        pytest.param(_constraints(), "unresolved_constraint", id="no-constraints"),
        pytest.param(
            _constraints(0, checkout=_checkout(variant="Q999-OTHER")),
            "unresolved_constraint",
            id="only-allowed-merchants-variant-out-of-scope",
        ),
        pytest.param(_constraints(1), "unresolved_constraint", id="only-line-items"),
        pytest.param(_constraints(0, 1, 1), "unresolved_constraint", id="duplicated-type"),
        pytest.param(_two_units_required, "unresolved_constraint", id="line-items-quantity-2"),
        pytest.param(
            _with_claims(checkout_hash="A" * 43), "unresolved_constraint", id="prefixed-claim"
        ),
    ],
)
def test_each_alteration_is_rejected_with_its_ap2_code(
    build: Callable[[Chain], tuple[str, dict[str, Any] | None]], code: str
) -> None:
    chain = Chain()
    token, checkout = build(chain)
    _rejects(chain, token, code, checkout)


def test_credentials_bindings_and_clock_follow_the_profile() -> None:
    chain = Chain()
    checkout_jwt = chain.checkout_jwt()
    open_token = chain.open()

    untrusted_root = chain.close(chain.open(key=_key()), checkout_jwt)
    _rejects(chain, untrusted_root, "invalid_credential")
    issuer_jwt_hash = chain.close(open_token, checkout_jwt, hash_mode="issuer_jwt_hash")
    _rejects(chain, issuer_jwt_hash, "invalid_credential")
    _rejects(
        chain, chain.close(open_token, checkout_jwt, aud="https://other.test"), "invalid_credential"
    )
    _rejects(chain, chain.close(open_token, checkout_jwt, nonce="B" * 43), "invalid_credential")

    expired_open = chain.open(chain.open_claims(iat=chain.now - 600, exp=chain.now - 1))
    _rejects(chain, chain.close(expired_open, checkout_jwt), "invalid_credential")
    without_exp = chain.open_claims()
    del without_exp["exp"]
    _rejects(chain, chain.close(chain.open(without_exp), checkout_jwt), "invalid_credential")
    # Within the SDK's 60 s skew, but the profile checks exp without slack.
    _rejects(
        chain,
        chain.close(open_token, checkout_jwt, iat=chain.now - 330, exp=chain.now - 30),
        "invalid_credential",
    )
    future = chain.close(open_token, checkout_jwt, iat=chain.now + 61, exp=chain.now + 300)
    _rejects(chain, future, "invalid_credential")
    long_lived = chain.close(open_token, checkout_jwt, exp=chain.now + 301)
    _rejects(chain, long_lived, "invalid_mandate")
    # A further delegation hop (typ kb+sd-jwt+kb, with cnf) instead of the terminal closure.
    delegated = MandateClient().present(
        chain.agent,
        open_token,
        [OpenCheckoutMandate.model_validate(chain.open_claims())],
        aud=ISSUER,
        nonce=_hash(checkout_jwt),
    )
    _rejects(chain, delegated, "invalid_credential")

    other_merchant = MerchantCheckoutSigner(
        ec.generate_private_key(ec.SECP256R1())
        .private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        .decode(),
        issuer=ISSUER,
    )
    forged_checkout = chain.close(open_token, other_merchant.sign(_checkout(), revision=3))
    _rejects(chain, forged_checkout, "invalid_credential")


def test_chain_shape_and_reference_of_unparseable_segments() -> None:
    chain = Chain()
    token = chain.valid()
    _rejects(chain, f"{token}~~{token.split('~~')[1]}", "invalid_credential")
    with pytest.raises(Ap2MandateError) as caught:
        chain.verify(f"{token.split('~~')[0]}~~not-a-token")
    assert caught.value.code == "invalid_credential"
    assert caught.value.reference == _hash("not-a-token")


def test_checkout_must_have_one_line_with_quantity_one_and_be_projectable() -> None:
    chain = Chain()
    two_units = _checkout(quantity=2)
    _rejects(chain, chain.valid(two_units), "invalid_mandate", two_units)
    two_lines = _checkout()
    two_lines["line_items"].append({**two_lines["line_items"][0], "id": "li_second"})
    _rejects(chain, chain.valid(two_lines), "invalid_mandate", two_lines)
    unnamed = _checkout()
    del unnamed["line_items"][0]["item"]["name"]
    _rejects(chain, chain.valid(unnamed), "unresolved_constraint", unnamed)


def test_idempotent_replay_accepts_the_same_expired_closure_on_the_completed_checkout() -> None:
    chain = Chain()
    token = chain.valid()
    completed = {**_checkout(), "status": "completed"}
    chain.now += 3_600
    assert chain.verify(token, completed) == (3, _reference(token))
    _rejects(chain, token, "invalid_credential")
    _rejects(chain, token, "invalid_mandate", {**completed, "id": "chk_other"})


def test_misconfigured_trust_root_means_mandates_are_not_supported() -> None:
    chain = Chain()
    with pytest.raises(Ap2MandateError) as caught:
        chain.verify(chain.valid(), trusted=chain.provider.export())
    assert caught.value.code == "mandates_not_supported"


def test_error_receipt_is_a_signed_checkout_receipt_error() -> None:
    chain = Chain()
    receipt = chain.signer.error_receipt(
        reference="ref", error="invalid_mandate", description="Checkout violates the mandate"
    )
    claims = jwt.decode(
        receipt,
        jwt.PyJWK.from_dict(chain.signer.jwk).key,
        algorithms=["ES256"],
        issuer=ISSUER,
        options={"verify_aud": False},
    )
    assert jwt.get_unverified_header(receipt)["kid"] == chain.signer.jwk["kid"]
    parsed = CheckoutReceiptError.model_validate(claims)
    assert (parsed.status, parsed.reference, parsed.error, parsed.iss) == (
        "Error",
        "ref",
        "invalid_mandate",
        ISSUER,
    )
    assert parsed.order_id is None


class _StubCheckout:
    def __init__(self, snapshot: dict[str, Any], revision: int = 3) -> None:
        self.snapshot = snapshot
        self.revision = revision
        self.calls = 0

    def get_with_revision(self, checkout_id: str) -> tuple[dict[str, Any], int]:
        assert checkout_id == "chk_hnp"
        return self.snapshot, self.revision

    def complete(
        self,
        checkout_id: str,
        payload: dict[str, Any],
        idempotency_key: str,
        *,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        assert (checkout_id, idempotency_key, expected_revision) == ("chk_hnp", "op_1:complete", 3)
        assert payload == {"payment_data": {"handler_id": "tesis_sandbox"}}
        self.calls += 1
        self.snapshot = {**self.snapshot, "status": "completed"}
        self.revision = 4
        return {**self.snapshot, "order": {"id": "ord_hnp"}}


def _api(
    monkeypatch: pytest.MonkeyPatch, chain: Chain, stub: _StubCheckout, *, required: bool = True
) -> TestClient:
    monkeypatch.setattr(
        "commerce_lab.api.get_settings",
        lambda: Settings(
            merchant_ap2_required=required,
            merchant_ap2_agent_jwk_json=SecretStr(chain.trusted),
            payment_merchant_id=MERCHANT_ID,
        ),
    )
    context = ExecutionContext(
        actor_id="buyer-agent",
        run_id="00000000-0000-0000-0000-000000000001",
        request_id="request-test",
        received_at=FIXTURE_NOW,
    )
    app.dependency_overrides[trusted_context] = lambda: context
    app.dependency_overrides[acp_checkout] = lambda: stub
    app.dependency_overrides[ap2_signer] = lambda: chain.signer
    return TestClient(app)


PATH = "/checkout_sessions/chk_hnp/ap2/complete"
HEADERS = {"API-Version": "2026-04-17", "Idempotency-Key": "op_1:complete"}


def _body(token: str) -> dict[str, Any]:
    return {"checkout_mandate": token, "payment_data": {"handler_id": "tesis_sandbox"}}


def test_ap2_complete_turns_a_valid_chain_into_an_order(monkeypatch: pytest.MonkeyPatch) -> None:
    chain = Chain()
    stub = _StubCheckout(_checkout())
    token = chain.valid()
    try:
        client = _api(monkeypatch, chain, stub)
        response = client.post(PATH, headers=HEADERS, json=_body(token))
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "no-store"
        assert response.json()["checkout"]["order"]["id"] == "ord_hnp"
        receipt = jwt.decode(
            response.json()["checkout_receipt_jwt"],
            jwt.PyJWK.from_dict(chain.signer.jwk).key,
            algorithms=["ES256"],
            issuer=ISSUER,
            options={"verify_aud": False},
        )
        assert receipt["status"] == "Success"
        assert receipt["reference"] == _reference(token)
        assert receipt["order_id"] == "ord_hnp"

        # Idempotent retry after the closure expired: same order, same reference.
        chain.now += 3_600
        replay = client.post(PATH, headers=HEADERS, json=_body(token))
        assert replay.status_code == 200, replay.text
        assert replay.json()["checkout"]["order"]["id"] == "ord_hnp"
        stub.revision = 5
        stale = client.post(PATH, headers=HEADERS, json=_body(token))
        assert stale.status_code == 422
        assert stale.json()["code"] == "invalid_mandate"
        assert stub.calls == 2
    finally:
        app.dependency_overrides.clear()


def test_ap2_complete_rejects_an_altered_chain_with_a_signed_error_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chain = Chain()
    out_of_scope = _checkout(variant="Q999-OTHER")
    stub = _StubCheckout(out_of_scope)
    token = chain.valid(out_of_scope)
    try:
        client = _api(monkeypatch, chain, stub)
        response = client.post(PATH, headers=HEADERS, json=_body(token))
        assert response.status_code == 422, response.text
        assert response.headers["cache-control"] == "no-store"
        body = response.json()
        assert set(body) == {"code", "checkout_receipt_jwt"}
        assert body["code"] == "invalid_mandate"
        claims = jwt.decode(
            body["checkout_receipt_jwt"],
            jwt.PyJWK.from_dict(chain.signer.jwk).key,
            algorithms=["ES256"],
            issuer=ISSUER,
            options={"verify_aud": False},
        )
        receipt = CheckoutReceiptError.model_validate(claims)
        assert (receipt.error, receipt.reference) == ("invalid_mandate", _reference(token))
        assert stub.calls == 0
    finally:
        app.dependency_overrides.clear()


def test_ap2_complete_without_ap2_answers_mandates_not_supported_without_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chain = Chain()
    stub = _StubCheckout(_checkout())
    try:
        client = _api(monkeypatch, chain, stub, required=False)
        response = client.post(PATH, headers=HEADERS, json=_body(chain.valid()))
        assert response.status_code == 503
        assert response.json() == {"code": "mandates_not_supported"}
        hp = client.post(PATH, headers=HEADERS, json=_body("header.payload.signature~"))
        assert hp.status_code == 503
        assert hp.json() == {"detail": "AP2 completion is not configured"}
        assert stub.calls == 0
    finally:
        app.dependency_overrides.clear()
