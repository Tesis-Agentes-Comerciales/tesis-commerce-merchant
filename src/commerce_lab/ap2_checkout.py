import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, Never, TypeGuard
from uuid import uuid4

import jwt
from ap2.sdk.constraints import check_checkout_constraints
from ap2.sdk.generated.checkout_mandate import CheckoutMandate
from ap2.sdk.generated.open_checkout_mandate import (
    AllowedMerchants,
    Item,
    LineItemRequirements,
    LineItems,
    OpenCheckoutMandate,
)
from ap2.sdk.generated.types.checkout import Checkout
from ap2.sdk.generated.types.merchant import Merchant
from ap2.sdk.mandate import MandateClient
from ap2.sdk.sdjwt import common, sd_jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from jwcrypto.common import JWException
from jwcrypto.jwk import JWK
from jwt.utils import base64url_encode
from pydantic import ValidationError

from commerce_lab.settings import get_settings

AP2_VERSION = "0.2"
SHOPPING_AGENT_AUDIENCE = "ap2-shopping-agent"

# Human Not Present profile `ap2-hnp/v1` (docs/ap2-profile.md in the agent repository).
CHAIN_SEPARATOR = "~~"
MERCHANT_NAME = "Tesis Commerce Merchant"
Ap2ErrorCode = Literal[
    "invalid_credential", "invalid_mandate", "unresolved_constraint", "mandates_not_supported"
]
_OPEN_VCT = "mandate.checkout.open.1"
_CLOSED_VCT = "mandate.checkout.1"
_ROOT_TYP = "example+sd-jwt"
_CLOSED_TYP = "kb+sd-jwt"
_HASH_ALGORITHMS = {None, "sha-256"}
_IAT_SKEW_SECONDS = 60
_CLOSED_MAX_LIFETIME_SECONDS = 300
_PROJECTED_STATUS = {"ready_for_payment": "ready_for_complete", "completed": "completed"}
_CHECKOUT_CONSTRAINTS = {
    "checkout.allowed_merchants": (AllowedMerchants, "allowed", Merchant),
    "checkout.line_items": (LineItems, "items", LineItemRequirements),
}


class Ap2MandateError(Exception):
    """A rejected HNP checkout mandate chain, with the AP2 code its error receipt carries."""

    def __init__(self, code: Ap2ErrorCode, description: str, reference: str) -> None:
        super().__init__(description)
        self.code: Ap2ErrorCode = code
        self.description = description
        self.reference = reference


def chain_reference(chain: str) -> str:
    """Receipt `reference`: `sd_hash` of the last segment, or SHA-256 of it if unparseable."""
    last = chain.rsplit(CHAIN_SEPARATOR, 1)[-1]
    try:
        return common.compute_sd_hash(common.parse_token(last))
    except ValueError:
        return base64url_encode(hashlib.sha256(last.encode()).digest()).decode()


def project_acp_checkout(checkout: dict[str, Any], *, merchant_id: str) -> Checkout:
    """Deterministic ACP→SDK projection for `check_checkout_constraints` (not UCP conformance)."""
    try:
        status = _PROJECTED_STATUS.get(checkout["status"])
        if status is None:
            raise ValueError("Checkout status is not projectable")
        return Checkout.model_validate(
            {
                "id": checkout["id"],
                "merchant": {"id": merchant_id, "name": MERCHANT_NAME},
                "line_items": [
                    {
                        "id": line["id"],
                        "item": {
                            "id": line["item"]["id"],
                            "title": line["item"]["name"],
                            "price": line["item"]["unit_amount"],
                        },
                        "quantity": line["quantity"],
                        "totals": [],
                    }
                    for line in checkout["line_items"]
                ],
                "status": status,
                "currency": checkout["currency"].upper(),
                "totals": [
                    {"type": total["type"], "amount": total["amount"]}
                    for total in checkout["totals"]
                ],
                "links": [],
            }
        )
    except (KeyError, TypeError, AttributeError, ValueError) as error:
        raise ValueError("Checkout is not projectable") from error


def _within(value: Any, model: type[Any]) -> bool:
    return isinstance(value, dict) and set(value) <= set(model.model_fields)


def _open_checkout_in_profile(claims: dict[str, Any]) -> bool:
    """Profile check before parsing: the SDK rejects unknown types and ignores unknown keys."""
    constraints = claims.get("constraints")
    if not _within(claims, OpenCheckoutMandate) or not isinstance(constraints, list):
        return False
    for constraint in constraints:
        kind = constraint.get("type") if isinstance(constraint, dict) else None
        if kind not in _CHECKOUT_CONSTRAINTS:
            return False
        model, members, member_model = _CHECKOUT_CONSTRAINTS[kind]
        entries = constraint.get(members)
        if not _within(constraint, model) or not isinstance(entries, list):
            return False
        for entry in entries:
            if not _within(entry, member_model):
                return False
            if member_model is LineItemRequirements:
                acceptable = entry.get("acceptable_items")
                if not isinstance(acceptable, list) or not acceptable:
                    return False
                if not all(_within(item, Item) for item in acceptable):
                    return False
    return True


def _is_int(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool)


def _trusted_agent_jwk(trusted_agent_jwk_json: str) -> JWK:
    trusted_jwk = json.loads(trusted_agent_jwk_json)
    if (
        not isinstance(trusted_jwk, dict)
        or trusted_jwk.get("kty") != "EC"
        or trusted_jwk.get("crv") != "P-256"
        or "d" in trusted_jwk
    ):
        raise ValueError("Trusted agent key must be a public P-256 JWK")
    return JWK.from_json(trusted_agent_jwk_json)


class MerchantCheckoutSigner:
    def __init__(
        self,
        private_key_pem: str,
        *,
        issuer: str,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        key = serialization.load_pem_private_key(private_key_pem.encode(), password=None)
        if not isinstance(key, ec.EllipticCurvePrivateKey) or not isinstance(
            key.curve, ec.SECP256R1
        ):
            raise ValueError("AP2 checkout signing requires an ES256 private key")
        self._key = key
        self._issuer = issuer
        self._clock = clock or (lambda: datetime.now(UTC))
        public = key.public_key()
        digest = hashlib.sha256(
            public.public_bytes(
                encoding=serialization.Encoding.DER,
                format=serialization.PublicFormat.SubjectPublicKeyInfo,
            )
        ).digest()
        self._kid = base64url_encode(digest).decode()
        numbers = public.public_numbers()
        self.jwk = {
            "kty": "EC",
            "crv": "P-256",
            "alg": "ES256",
            "use": "sig",
            "kid": self._kid,
            "x": base64url_encode(numbers.x.to_bytes(32, "big")).decode(),
            "y": base64url_encode(numbers.y.to_bytes(32, "big")).decode(),
        }

    def sign(self, checkout: dict[str, Any], *, revision: int = 0) -> str:
        if checkout.get("status") != "ready_for_payment":
            raise ValueError("Checkout is not ready for approval")
        if not isinstance(checkout.get("id"), str):
            raise ValueError("Checkout has no identifier")
        now = self._clock()
        claims = {
            "iss": self._issuer,
            "aud": SHOPPING_AGENT_AUDIENCE,
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=5)).timestamp()),
            "jti": str(uuid4()),
            "checkout": checkout,
            "checkout_revision": revision,
        }
        return jwt.encode(
            claims,
            self._key,
            algorithm="ES256",
            headers={"kid": self._kid, "typ": "JWT"},
        )

    def verify_mandate(
        self,
        token: str,
        *,
        checkout: dict[str, Any],
        trusted_agent_jwk_json: str,
    ) -> tuple[int, str]:
        try:
            verified = sd_jwt.verify(token, _trusted_agent_jwk(trusted_agent_jwk_json))
            contents = verified["delegate_payload"]
            if not isinstance(contents, list) or len(contents) != 1:
                raise ValueError("Invalid checkout mandate payload")
            mandate = CheckoutMandate.model_validate(contents[0])
            claims = jwt.decode(
                mandate.checkout_jwt,
                self._key.public_key(),
                algorithms=["ES256"],
                issuer=self._issuer,
                audience=SHOPPING_AGENT_AUDIENCE,
                options={
                    "require": ["iss", "aud", "iat", "exp", "jti", "checkout", "checkout_revision"],
                    "verify_exp": checkout.get("status") != "completed",
                },
            )
            now = int(self._clock().timestamp())
            digest = base64url_encode(
                hashlib.sha256(mandate.checkout_jwt.encode("ascii")).digest()
            ).decode()
            revision = claims["checkout_revision"]
            if (
                mandate.iat is None
                or mandate.exp is None
                or mandate.iat > now + 60
                or mandate.exp <= mandate.iat
                or mandate.exp - mandate.iat > 300
                or (mandate.exp <= now and checkout.get("status") != "completed")
                or mandate.checkout_hash != digest
                or (
                    claims["checkout"] != checkout
                    and not (
                        checkout.get("status") == "completed"
                        and claims["checkout"].get("id") == checkout.get("id")
                    )
                )
                or not isinstance(revision, int)
                or isinstance(revision, bool)
                or revision < 0
            ):
                raise ValueError("Mandate does not match current checkout")
            return revision, common.compute_sd_hash(common.parse_token(token))
        except (KeyError, TypeError, ValueError, jwt.PyJWTError, JWException) as error:
            raise ValueError("Invalid AP2 checkout mandate") from error

    def verify_mandate_chain(
        self,
        chain: str,
        *,
        checkout: dict[str, Any],
        trusted_agent_jwk_json: str,
        merchant_id: str,
    ) -> tuple[int, str]:
        """Verify an HNP `<open SD-JWT>~~<closed KB-SD-JWT>` checkout chain.

        Returns the approved checkout revision and the receipt reference, or raises
        `Ap2MandateError` with the profile's code. The trust root is the agent provider's
        pinned JWK, the same key that signs HP mandates.
        """
        reference = chain_reference(chain)

        def reject(code: Ap2ErrorCode, description: str) -> Never:
            raise Ap2MandateError(code, description, reference)

        try:
            root = _trusted_agent_jwk(trusted_agent_jwk_json)
        except (ValueError, JWException):
            reject("mandates_not_supported", "The agent provider key is not configured.")
        root_kid = root.thumbprint()

        def root_key(token: common.ParsedToken) -> JWK:
            header = token.header
            if (
                "x5c" in header
                or header.get("kid") != root_kid
                or header.get("alg") != "ES256"
                or header.get("typ") != _ROOT_TYP
                or token.sd_alg not in _HASH_ALGORITHMS
            ):
                raise ValueError("Open mandate is not signed by the agent provider")
            return root

        segments = chain.split(CHAIN_SEPARATOR)
        try:
            if len(segments) != 2 or not all(segments):
                raise ValueError("A checkout chain has exactly two segments")
            closed_token = common.parse_token(segments[1])
        except ValueError:
            reject("invalid_credential", "The checkout mandate chain is malformed.")
        replay = checkout.get("status") == "completed"
        # A retry on the completed checkout is checked at the closure time (HP semantics).
        now = closed_token.payload.get("iat") if replay else int(self._clock().timestamp())
        if not _is_int(now):
            reject("invalid_credential", "The closed mandate has no issue time.")
        header = closed_token.header
        try:
            if (
                header.get("alg") != "ES256"
                or header.get("typ") != _CLOSED_TYP
                or closed_token.sd_alg not in _HASH_ALGORITHMS
            ):
                raise ValueError("Unexpected closed mandate header")
            payloads = MandateClient().verify(
                chain,
                root_key,
                expected_aud=self._issuer,
                clock_skew_seconds=_IAT_SKEW_SECONDS,
                current_time=now,
            )
        except Exception:
            reject("invalid_credential", "The checkout mandate chain does not verify.")
        if (
            "sd_hash" not in closed_token.payload
            or "issuer_jwt_hash" in closed_token.payload
            or not isinstance(payloads, list)
            or len(payloads) != 2
        ):
            reject("invalid_credential", "The closed mandate must bind its open one by sd_hash.")
        open_claims, closed_claims = payloads
        if open_claims.get("vct") != _OPEN_VCT or closed_claims.get("vct") != _CLOSED_VCT:
            reject("invalid_credential", "Unexpected mandate type for the checkout chain.")
        for claims in (open_claims, closed_claims):
            iat, exp = claims.get("iat"), claims.get("exp")
            if not _is_int(iat) or not _is_int(exp) or iat > now + _IAT_SKEW_SECONDS:
                reject("invalid_credential", "The mandate chain has missing or future time claims.")
            if now >= exp or exp <= iat:
                reject("invalid_credential", "The mandate chain has expired.")
        if not _open_checkout_in_profile(open_claims):
            reject("unresolved_constraint", "The open mandate has constraints outside the profile.")
        try:
            open_mandate = OpenCheckoutMandate.model_validate(open_claims)
        except ValidationError:
            reject("unresolved_constraint", "The open mandate has constraints outside the profile.")
        try:
            closed = CheckoutMandate.model_validate(closed_claims)
        except ValidationError:
            reject("invalid_credential", "The closed mandate is malformed.")
        if closed_claims["exp"] - closed_claims["iat"] > _CLOSED_MAX_LIFETIME_SECONDS:
            reject("invalid_mandate", "The closed mandate lives longer than 300 seconds.")
        digest = base64url_encode(hashlib.sha256(closed.checkout_jwt.encode()).digest()).decode()
        if closed.checkout_hash != digest:
            reject("invalid_mandate", "checkout_hash does not match the presented checkout JWT.")
        if closed_token.payload.get("nonce") != digest:
            reject("invalid_credential", "The closed mandate nonce is not the checkout hash.")
        try:
            signed = jwt.decode(
                closed.checkout_jwt,
                self._key.public_key(),
                algorithms=["ES256"],
                issuer=self._issuer,
                audience=SHOPPING_AGENT_AUDIENCE,
                options={
                    "require": ["iss", "aud", "iat", "exp", "jti", "checkout", "checkout_revision"],
                    "verify_exp": not replay,
                },
            )
            revision = signed["checkout_revision"]
            if not _is_int(revision) or revision < 0 or not isinstance(signed["checkout"], dict):
                raise ValueError("Invalid checkout revision")
        except (ValueError, UnicodeError, jwt.PyJWTError):
            reject("invalid_credential", "The checkout JWT is not a valid merchant signature.")
        matches = (
            signed["checkout"].get("id") == checkout.get("id")
            if replay
            else signed["checkout"] == checkout
        )
        if not matches:
            reject("invalid_mandate", "The closed mandate does not match the current checkout.")
        try:
            projected = project_acp_checkout(checkout, merchant_id=merchant_id)
        except ValueError:
            reject("unresolved_constraint", "The checkout cannot be projected for AP2.")
        if len(projected.line_items) != 1 or projected.line_items[0].quantity != 1:
            reject("invalid_mandate", "An HNP checkout has one line with quantity 1.")
        if check_checkout_constraints(open_mandate, projected):
            reject("invalid_mandate", "The checkout violates the open mandate constraints.")
        return revision, reference

    def error_receipt(self, *, reference: str, error: Ap2ErrorCode, description: str) -> str:
        claims = {
            "status": "Error",
            "iss": self._issuer,
            "iat": int(self._clock().timestamp()),
            "reference": reference,
            "error": error,
            "error_description": description,
        }
        return jwt.encode(claims, self._key, algorithm="ES256", headers={"kid": self._kid})

    def receipt(self, *, reference: str, order_id: str) -> str:
        claims = {
            "status": "Success",
            "iss": self._issuer,
            "iat": int(self._clock().timestamp()),
            "reference": reference,
            "order_id": order_id,
        }
        return jwt.encode(claims, self._key, algorithm="ES256", headers={"kid": self._kid})


def configured_checkout_signer() -> MerchantCheckoutSigner:
    settings = get_settings()
    secret = settings.merchant_ap2_private_key_pem
    if secret is None:
        raise RuntimeError("AP2 checkout signing is not configured")
    return MerchantCheckoutSigner(
        secret.get_secret_value().replace("\\n", "\n"), issuer=settings.acp_api_base_url
    )
