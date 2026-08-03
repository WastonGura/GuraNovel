"""Provider-neutral gateway for strict structured model output."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import json
import math
import re
from typing import Annotated, ForwardRef, Generic, Literal, Protocol, TypeVar
from typing import get_args, get_origin
from typing import runtime_checkable

from pydantic import BaseModel

from app.llm.errors import (
    ProviderConfigurationError,
    ProviderInvalidOutputError,
    ProviderRateLimitedError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)


MAX_PROVENANCE_TOKEN_COUNT = 1_000_000_000
_MAX_PROFILE_INPUT_CHARS = 4_000_000
_MAX_PROFILE_OUTPUT_BYTES = 16_000_000
_INVALID_DECODE = object()
_MACHINE_IDENTIFIER_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_MODEL_IDENTIFIER_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}")
_SENSITIVE_SK_SEGMENT_PATTERN = re.compile(r"(?:^|[._:/-])sk[-_]", re.IGNORECASE)
_MODEL_SENSITIVE_MATERIAL_PATTERN = re.compile(
    r"(?:^|[._:/-])"
    r"(?:api[-_]?key|apikey|authorization|bearer|token|secret|redacted)"
    r"(?=$|[._:/-])",
    re.IGNORECASE,
)


def _reject_duplicate_json_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def _reject_non_finite_json_constant(_: str) -> object:
    raise ValueError("non-finite JSON constants are forbidden")


def _validate_finite_payload(value: object) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("non-finite numbers are forbidden")
    if isinstance(value, dict):
        for key, item in value.items():
            _validate_finite_payload(key)
            _validate_finite_payload(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _validate_finite_payload(item)


def _decode_strict_json(value: str | bytes) -> object:
    decoded = json.loads(
        value,
        object_pairs_hook=_reject_duplicate_json_keys,
        parse_constant=_reject_non_finite_json_constant,
    )
    _validate_finite_payload(decoded)
    return decoded


def _models_in_annotation(annotation: object) -> set[type[BaseModel]]:
    if isinstance(annotation, (str, ForwardRef)):
        raise ValueError("output model annotations must be fully resolved")
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return {annotation}
    origin = get_origin(annotation)
    if origin is None:
        return set()
    arguments = get_args(annotation)
    if origin is Literal:
        return set()
    if origin is Annotated:
        arguments = arguments[:1]
    models: set[type[BaseModel]] = set()
    for argument in arguments:
        models.update(_models_in_annotation(argument))
    return models


def _reachable_output_models(root: type[BaseModel]) -> set[type[BaseModel]]:
    pending = [root]
    models: set[type[BaseModel]] = set()
    while pending:
        model = pending.pop()
        if model in models:
            continue
        models.add(model)
        for model_field in model.model_fields.values():
            pending.extend(_models_in_annotation(model_field.annotation) - models)
    return models


def _validate_reachable_output_models(root: object) -> None:
    try:
        if not isinstance(root, type) or not issubclass(root, BaseModel):
            raise ValueError("output schema must be a Pydantic model")
        models = _reachable_output_models(root)
        if any(
            model.model_config.get("strict") is not True
            or model.model_config.get("extra") != "forbid"
            for model in models
        ):
            raise ValueError(
                "all reachable output models must be strict and forbid extra fields"
            )
    except ValueError:
        raise
    except Exception:
        raise ValueError("output model graph is invalid") from None


def _resolved_root_schema(schema: dict[str, object]) -> dict[str, object]:
    reference = schema.get("$ref")
    if not isinstance(reference, str):
        return schema
    prefix = "#/$defs/"
    definitions = schema.get("$defs")
    if not reference.startswith(prefix) or not isinstance(definitions, dict):
        raise ValueError("output schema has an unsupported root reference")
    resolved = definitions.get(reference.removeprefix(prefix))
    if not isinstance(resolved, dict):
        raise ValueError("output schema root reference cannot be resolved")
    return resolved


_JSON_SCALAR_TYPES = frozenset({"string", "number", "integer", "boolean", "null"})


def _validate_schema_child(node: object) -> None:
    if not isinstance(node, dict):
        raise ValueError("schema nodes must be explicit objects")
    _validate_constrained_schema_node(node)


def _validate_constrained_schema_node(node: dict[str, object]) -> None:
    """Reject every schema node that could accept an unconstrained JSON value."""
    if "contentSchema" in node or node.get("contentMediaType") == "application/json":
        raise ValueError("nested JSON string schemas are forbidden")
    constrained = False
    schema_type = node.get("type")
    if schema_type is not None:
        constrained = True
        if not isinstance(schema_type, str):
            raise ValueError("schema types must be explicit strings")
        if schema_type == "object":
            if node.get("additionalProperties") is not False:
                raise ValueError("object schemas must forbid additional properties")
            properties = node.get("properties", {})
            if not isinstance(properties, dict):
                raise ValueError("object properties must be an object")
            for child in properties.values():
                _validate_schema_child(child)
            pattern_properties = node.get("patternProperties", {})
            if not isinstance(pattern_properties, dict):
                raise ValueError("pattern properties must be an object")
            for child in pattern_properties.values():
                _validate_schema_child(child)
            if "propertyNames" in node:
                _validate_schema_child(node["propertyNames"])
        elif schema_type == "array":
            has_constrained_items = False
            if "items" in node:
                items = node["items"]
                if items is False:
                    has_constrained_items = True
                else:
                    _validate_schema_child(items)
                    has_constrained_items = True
            if "prefixItems" in node:
                prefix_items = node["prefixItems"]
                if not isinstance(prefix_items, list) or not prefix_items:
                    raise ValueError("array prefix items must be a non-empty list")
                for child in prefix_items:
                    _validate_schema_child(child)
                has_constrained_items = True
            if not has_constrained_items:
                raise ValueError("array schemas must constrain their items")
        elif schema_type not in _JSON_SCALAR_TYPES:
            raise ValueError("unsupported schema type")

    reference = node.get("$ref")
    if reference is not None:
        if not isinstance(reference, str) or not reference.startswith("#/$defs/"):
            raise ValueError("schema references must use local definitions")
        constrained = True

    for keyword in ("anyOf", "allOf", "oneOf"):
        if keyword not in node:
            continue
        alternatives = node[keyword]
        if not isinstance(alternatives, list) or not alternatives:
            raise ValueError("schema combinators must have constrained alternatives")
        for child in alternatives:
            _validate_schema_child(child)
        constrained = True

    if "enum" in node:
        enum_values = node["enum"]
        if not isinstance(enum_values, list) or not enum_values:
            raise ValueError("schema enums must be non-empty")
        constrained = True
    if "const" in node:
        constrained = True
    if not constrained:
        raise ValueError("unconstrained schema nodes are forbidden")

    definitions = node.get("$defs", {})
    if not isinstance(definitions, dict):
        raise ValueError("schema definitions must be an object")
    for child in definitions.values():
        _validate_schema_child(child)


def _closed_json_schema(root: type[BaseModel]) -> dict[str, object]:
    schema = root.model_json_schema()
    if not isinstance(schema, dict):
        raise ValueError("output schema must be an object")
    _validate_constrained_schema_node(schema)
    for model in _reachable_output_models(root):
        model_schema = model.model_json_schema()
        if not isinstance(model_schema, dict):
            raise ValueError("output model schema must be an object")
        resolved = _resolved_root_schema(model_schema)
        if (
            resolved.get("type") != "object"
            or resolved.get("additionalProperties") is not False
        ):
            raise ValueError("output model schemas must forbid additional properties")
    return schema


def _validate_machine_identifier(value: object) -> str:
    if (
        not isinstance(value, str)
        or _MACHINE_IDENTIFIER_PATTERN.fullmatch(value) is None
        or _SENSITIVE_SK_SEGMENT_PATTERN.search(value) is not None
        or _MODEL_SENSITIVE_MATERIAL_PATTERN.search(value) is not None
    ):
        raise ValueError("value must be a safe bounded machine identifier")
    return value


def validate_model_identifier(value: object) -> str:
    """Return a safe, bounded model identifier suitable for trusted provenance."""
    if (
        not isinstance(value, str)
        or _MODEL_IDENTIFIER_PATTERN.fullmatch(value) is None
        or "://" in value
        or _SENSITIVE_SK_SEGMENT_PATTERN.search(value) is not None
        or _MODEL_SENSITIVE_MATERIAL_PATTERN.search(value) is not None
    ):
        raise ValueError("model identifier must be a safe bounded identifier")
    return value


@dataclass(frozen=True)
class StructuredOutputProvenance:
    """Trusted, server-owned structured-call provenance."""

    provider_kind: str
    model_identifier: str
    prompt_template_version: str

    def __post_init__(self) -> None:
        _validate_machine_identifier(self.provider_kind)
        validate_model_identifier(self.model_identifier)
        _validate_machine_identifier(self.prompt_template_version)

    def to_payload(
        self, *, input_tokens: int | None = None, output_tokens: int | None = None
    ) -> dict[str, str | int]:
        _validate_machine_identifier(self.provider_kind)
        validate_model_identifier(self.model_identifier)
        _validate_machine_identifier(self.prompt_template_version)
        for value in (input_tokens, output_tokens):
            if value is not None and (
                type(value) is not int
                or not 0 <= value <= MAX_PROVENANCE_TOKEN_COUNT
            ):
                raise ValueError("token counters must be bounded integers")
        payload: dict[str, str | int] = {
            "provider_kind": self.provider_kind,
            "model_identifier": self.model_identifier,
            "prompt_template_version": self.prompt_template_version,
        }
        if input_tokens is not None:
            payload["input_tokens"] = input_tokens
        if output_tokens is not None:
            payload["output_tokens"] = output_tokens
        return payload


OutputT = TypeVar("OutputT", bound=BaseModel)


@dataclass(frozen=True)
class StructuredOutputProfile(Generic[OutputT]):
    """A server-owned prompt, model, schema, and resource policy."""

    profile_id: str
    provider_kind: str
    model_identifier: str
    prompt_template_version: str
    system_prompt: str = field(repr=False)
    output_schema_name: str
    output_schema: type[OutputT]
    timeout_seconds: float = 30.0
    max_input_chars: int = 1_000_000
    max_output_bytes: int = 1_000_000

    def __post_init__(self) -> None:
        _validate_machine_identifier(self.profile_id)
        StructuredOutputProvenance(
            self.provider_kind, self.model_identifier, self.prompt_template_version
        )
        _validate_machine_identifier(self.output_schema_name)
        if (
            not isinstance(self.system_prompt, str)
            or not self.system_prompt
            or "\x00" in self.system_prompt
            or len(self.system_prompt) > 32_768
        ):
            raise ValueError("system prompt must be bounded text")
        _validate_reachable_output_models(self.output_schema)
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, (int, float))
            or not 0 < self.timeout_seconds <= 120
        ):
            raise ValueError("timeout must be greater than 0 and no more than 120 seconds")
        for value, upper, label in (
            (self.max_input_chars, _MAX_PROFILE_INPUT_CHARS, "input bound"),
            (self.max_output_bytes, _MAX_PROFILE_OUTPUT_BYTES, "output bound"),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= upper:
                raise ValueError(f"{label} must be a positive bounded integer")

    @property
    def provenance(self) -> StructuredOutputProvenance:
        return StructuredOutputProvenance(
            self.provider_kind, self.model_identifier, self.prompt_template_version
        )


@dataclass(frozen=True)
class StructuredOutputRequest:
    """Capability-authored input; it contains no provider authority."""

    profile_id: str
    user_prompt: str = field(repr=False)

    def __post_init__(self) -> None:
        _validate_machine_identifier(self.profile_id)
        if not isinstance(self.user_prompt, str) or "\x00" in self.user_prompt:
            raise ValueError("user prompt must be text without NUL characters")


@dataclass(frozen=True)
class StructuredTransportRequest:
    """Fully composed request passed to a transport implementation."""

    profile_id: str
    model_identifier: str
    output_schema_name: str
    output_schema: dict[str, object] = field(repr=False)
    max_output_bytes: int
    timeout_seconds: float
    system_prompt: str = field(repr=False)
    user_prompt: str = field(repr=False)


@dataclass(frozen=True)
class StructuredTransportResponse:
    """Untrusted raw transport result and optional accounting."""

    payload: object = field(repr=False)
    input_tokens: object = field(default=None, repr=False)
    output_tokens: object = field(default=None, repr=False)


@runtime_checkable
class StructuredOutputTransport(Protocol):
    async def call(self, request: StructuredTransportRequest) -> StructuredTransportResponse:
        """Return raw structured output or raise a safe provider error."""


@dataclass(frozen=True)
class StructuredOutputResponse(Generic[OutputT]):
    result: OutputT = field(repr=False)
    provenance: StructuredOutputProvenance
    input_tokens: int | None = field(default=None, repr=False)
    output_tokens: int | None = field(default=None, repr=False)


_SAFE_PROVIDER_ERRORS = (
    ProviderConfigurationError,
    ProviderInvalidOutputError,
    ProviderRateLimitedError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)


def _normalize_safe_provider_error(error: Exception) -> Exception:
    try:
        error_type = type(error)
        if issubclass(error_type, ProviderConfigurationError):
            return ProviderConfigurationError()
        if issubclass(error_type, ProviderInvalidOutputError):
            return ProviderInvalidOutputError()
        if issubclass(error_type, ProviderRateLimitedError):
            return ProviderRateLimitedError()
        if issubclass(error_type, ProviderTimeoutError):
            return ProviderTimeoutError()
        if issubclass(error_type, ProviderUnavailableError):
            return ProviderUnavailableError()
    except Exception:
        pass
    return ProviderUnavailableError()


class StructuredOutputGateway(Generic[OutputT]):
    """Compose one server profile, call a transport, and validate before returning."""

    __slots__ = ("__profile", "__transport")

    def __init__(
        self, profile: StructuredOutputProfile[OutputT], transport: StructuredOutputTransport
    ) -> None:
        if not isinstance(profile, StructuredOutputProfile) or not isinstance(
            transport, StructuredOutputTransport
        ):
            raise ProviderConfigurationError()
        self.__profile = profile
        self.__transport = transport

    @property
    def profile_id(self) -> str:
        """Expose only the non-authoritative identifier needed by capability adapters."""
        return self.__profile.profile_id

    async def call(self, request: StructuredOutputRequest) -> StructuredOutputResponse[OutputT]:
        if (
            not isinstance(request, StructuredOutputRequest)
            or request.profile_id != self.__profile.profile_id
            or len(request.user_prompt) > self.__profile.max_input_chars
        ):
            raise ProviderInvalidOutputError()
        try:
            output_schema = _closed_json_schema(self.__profile.output_schema)
        except Exception:
            output_schema = None
        if output_schema is None:
            raise ProviderInvalidOutputError() from None
        transport_request = StructuredTransportRequest(
            profile_id=self.__profile.profile_id,
            model_identifier=self.__profile.model_identifier,
            output_schema_name=self.__profile.output_schema_name,
            output_schema=output_schema,
            max_output_bytes=self.__profile.max_output_bytes,
            timeout_seconds=self.__profile.timeout_seconds,
            system_prompt=self.__profile.system_prompt,
            user_prompt=request.user_prompt,
        )
        provider_error: Exception | None = None
        try:
            async with asyncio.timeout(self.__profile.timeout_seconds):
                raw_response = await self.__transport.call(transport_request)
        except _SAFE_PROVIDER_ERRORS as error:
            provider_error = _normalize_safe_provider_error(error)
        except TimeoutError:
            provider_error = ProviderTimeoutError()
        except Exception:
            provider_error = ProviderUnavailableError()
        if provider_error is not None:
            raise provider_error from None
        response_fields: tuple[object, object, object] | None = None
        try:
            if type(raw_response) is not StructuredTransportResponse:
                raise TypeError("transport response must use the exact response envelope")
            response_fields = (
                raw_response.payload,
                raw_response.input_tokens,
                raw_response.output_tokens,
            )
        except Exception:
            pass
        if response_fields is None:
            raise ProviderInvalidOutputError() from None
        raw_payload, raw_input_tokens, raw_output_tokens = response_fields
        payload = self._decode_payload(raw_payload)
        try:
            result = self.__profile.output_schema.model_validate(payload, strict=True)
        except Exception:
            result = None
        if result is None:
            raise ProviderInvalidOutputError() from None
        return StructuredOutputResponse(
            result=result,
            provenance=self.__profile.provenance,
            input_tokens=self._bounded_usage(raw_input_tokens),
            output_tokens=self._bounded_usage(raw_output_tokens),
        )

    def _decode_payload(self, raw_payload: object) -> object:
        decoded: object
        try:
            if type(raw_payload) is str:
                if len(raw_payload) > self.__profile.max_output_bytes:
                    raise ValueError("oversized")
                encoded = raw_payload.encode("utf-8")
                if len(encoded) > self.__profile.max_output_bytes:
                    raise ValueError("oversized")
                decoded = _decode_strict_json(raw_payload)
            elif type(raw_payload) is bytes:
                if len(raw_payload) > self.__profile.max_output_bytes:
                    raise ValueError("oversized")
                decoded = _decode_strict_json(raw_payload)
            else:
                raise TypeError("transport payload must be exact raw JSON text or bytes")
        except Exception:
            decoded = _INVALID_DECODE
        if decoded is _INVALID_DECODE:
            raise ProviderInvalidOutputError() from None
        return decoded

    @staticmethod
    def _bounded_usage(value: object) -> int | None:
        if type(value) is not int:
            return None
        return value if 0 <= value <= MAX_PROVENANCE_TOKEN_COUNT else None


__all__ = [
    "MAX_PROVENANCE_TOKEN_COUNT",
    "StructuredOutputGateway",
    "StructuredOutputProfile",
    "StructuredOutputProvenance",
    "StructuredOutputRequest",
    "StructuredOutputResponse",
    "StructuredOutputTransport",
    "StructuredTransportRequest",
    "StructuredTransportResponse",
    "validate_model_identifier",
]
