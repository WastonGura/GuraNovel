from __future__ import annotations

import asyncio
import dataclasses
from typing import Any, Literal

import pytest
from pydantic import BaseModel, ConfigDict, Json, model_validator

from app.llm import (
    GatewayChapterGenerationProvider,
    ProviderInvalidOutputError,
    ProviderRateLimitedError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    StructuredOutputGateway,
    StructuredOutputProfile,
    StructuredOutputRequest,
)
from app.llm.fake_provider import FakeStructuredOutputTransport
from app.llm.gateway import StructuredTransportResponse


class _ExampleOutput(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    answer: str


class _EvilInt(int):
    def __repr__(self) -> str:
        return "evil-usage-secret"


def _profile(**overrides: object) -> StructuredOutputProfile[_ExampleOutput]:
    values: dict[str, object] = {
        "profile_id": "reader_initial_v1",
        "provider_kind": "fake",
        "model_identifier": "deterministic-fake-v1",
        "prompt_template_version": "reader-initial-v1",
        "system_prompt": "Return the requested structured result.",
        "output_schema_name": "reader_initial_output",
        "output_schema": _ExampleOutput,
        "timeout_seconds": 30,
        "max_input_chars": 4096,
        "max_output_bytes": 4096,
    }
    values.update(overrides)
    return StructuredOutputProfile(**values)  # type: ignore[arg-type]


@pytest.mark.anyio
async def test_gateway_returns_strict_typed_output_and_trusted_provenance() -> None:
    transport = FakeStructuredOutputTransport(
        payload={"answer": "keep"},
        input_tokens=7,
        output_tokens=11,
    )
    gateway = StructuredOutputGateway(_profile(), transport)

    response = await gateway.call(
        StructuredOutputRequest("reader_initial_v1", "[S001] Chapter text")
    )

    assert response.result == _ExampleOutput(answer="keep")
    assert response.input_tokens == 7
    assert response.output_tokens == 11
    assert response.provenance.to_payload(input_tokens=7, output_tokens=11) == {
        "provider_kind": "fake",
        "model_identifier": "deterministic-fake-v1",
        "prompt_template_version": "reader-initial-v1",
        "input_tokens": 7,
        "output_tokens": 11,
    }
    assert transport.requests[0].profile_id == "reader_initial_v1"
    assert transport.requests[0].output_schema_name == "reader_initial_output"
    assert transport.requests[0].output_schema["additionalProperties"] is False
    assert transport.requests[0].timeout_seconds == 30
    assert "keep" not in repr(response)
    assert "keep" not in repr(transport)
    with pytest.raises(ValueError):
        response.provenance.to_payload(input_tokens=-1)


def test_requests_and_profiles_are_immutable_and_hide_prompts_from_repr() -> None:
    profile = _profile(system_prompt="private system prompt")
    request = StructuredOutputRequest("reader_initial_v1", "private document body")

    assert "private system prompt" not in repr(profile)
    assert "private document body" not in repr(request)
    with pytest.raises(dataclasses.FrozenInstanceError):
        request.user_prompt = "changed"  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        profile.model_identifier = "changed"  # type: ignore[misc]


def test_gateway_and_capability_adapter_expose_only_safe_profile_identity() -> None:
    profile = _profile(
        system_prompt="private system prompt",
        model_identifier="private-model-authority",
    )
    transport = FakeStructuredOutputTransport(payload={"answer": "keep"})
    gateway = StructuredOutputGateway(profile, transport)
    adapter = GatewayChapterGenerationProvider(gateway)  # type: ignore[arg-type]

    assert gateway.profile_id == "reader_initial_v1"
    for target in (gateway, adapter):
        for attribute in (
            "profile",
            "transport",
            "model_identifier",
            "output_schema",
            "system_prompt",
            "_gateway",
        ):
            assert not hasattr(target, attribute)
    assert "private system prompt" not in repr(gateway)
    assert "private-model-authority" not in repr(gateway)


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {"answer": 1},
        {"answer": "keep", "extra": "no"},
        "not-json",
        '{"answer":1}',
        '{"answer":"keep","answer":"replace"}',
    ],
)
@pytest.mark.anyio
async def test_gateway_rejects_malformed_output_without_exposing_it(payload: object) -> None:
    opaque = "raw-secret-that-must-not-leak"
    if payload == "not-json":
        payload = opaque
    gateway = StructuredOutputGateway(_profile(), FakeStructuredOutputTransport(payload=payload))

    with pytest.raises(ProviderInvalidOutputError) as error:
        await gateway.call(StructuredOutputRequest("reader_initial_v1", "private prompt"))

    assert opaque not in str(error.value)
    assert "private prompt" not in str(error.value)


@pytest.mark.anyio
async def test_gateway_rejects_oversized_output_and_profile_mismatch() -> None:
    gateway = StructuredOutputGateway(
        _profile(max_output_bytes=64),
        FakeStructuredOutputTransport(payload={"answer": "x" * 128}),
    )
    with pytest.raises(ProviderInvalidOutputError):
        await gateway.call(StructuredOutputRequest("reader_initial_v1", "prompt"))

    with pytest.raises(ProviderInvalidOutputError):
        await gateway.call(StructuredOutputRequest("client_selected_profile", "prompt"))


@pytest.mark.anyio
async def test_gateway_rejects_non_raw_payload_without_serializing_it(
) -> None:
    serialization_calls = 0

    class SerializationProbe(dict[str, str]):
        def items(self):  # type: ignore[no-untyped-def]
            nonlocal serialization_calls
            serialization_calls += 1
            return super().items()

    class ProbeTransport:
        async def call(self, _: object) -> StructuredTransportResponse:
            return StructuredTransportResponse(payload=SerializationProbe(answer="keep"))

    gateway = StructuredOutputGateway(_profile(), ProbeTransport())
    with pytest.raises(ProviderInvalidOutputError):
        await gateway.call(StructuredOutputRequest("reader_initial_v1", "prompt"))

    assert serialization_calls == 0


@pytest.mark.anyio
async def test_gateway_pre_rejects_non_exact_or_huge_text_before_encoding_or_decoding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    encoding_calls = 0

    class EncodingProbe(str):
        def encode(self, *_: object, **__: object) -> bytes:
            nonlocal encoding_calls
            encoding_calls += 1
            return super().encode()

    class ProbeTransport:
        def __init__(self, payload: object) -> None:
            self.payload = payload

        async def call(self, _: object) -> StructuredTransportResponse:
            return StructuredTransportResponse(payload=self.payload)

    decode_calls = 0

    def forbidden_decode(_: object) -> object:
        nonlocal decode_calls
        decode_calls += 1
        raise AssertionError("oversized text must not be decoded")

    monkeypatch.setattr("app.llm.gateway._decode_strict_json", forbidden_decode)
    for payload, max_bytes in ((EncodingProbe('{"answer":"keep"}'), 4096), ("x" * 4097, 4096)):
        gateway = StructuredOutputGateway(
            _profile(max_output_bytes=max_bytes),
            ProbeTransport(payload),
        )
        with pytest.raises(ProviderInvalidOutputError):
            await gateway.call(StructuredOutputRequest("reader_initial_v1", "prompt"))

    assert encoding_calls == 0
    assert decode_calls == 0


@pytest.mark.anyio
async def test_gateway_accepts_bounded_raw_json_bytes() -> None:
    class BytesTransport:
        async def call(self, _: object) -> StructuredTransportResponse:
            return StructuredTransportResponse(payload=b'{"answer":"keep"}')

    response = await StructuredOutputGateway(_profile(), BytesTransport()).call(
        StructuredOutputRequest("reader_initial_v1", "prompt")
    )

    assert response.result.answer == "keep"


@pytest.mark.parametrize("counter", [-1, True, 1_000_000_001, "7", _EvilInt(7)])
@pytest.mark.anyio
async def test_gateway_drops_untrusted_unbounded_usage(counter: object) -> None:
    gateway = StructuredOutputGateway(
        _profile(),
        FakeStructuredOutputTransport(
            payload={"answer": "keep"}, input_tokens=counter, output_tokens=counter
        ),
    )

    response = await gateway.call(StructuredOutputRequest("reader_initial_v1", "prompt"))

    assert response.input_tokens is None
    assert response.output_tokens is None
    assert "evil-usage-secret" not in repr(response)


@pytest.mark.parametrize(
    ("failure", "error_type"),
    [
        ("timeout", ProviderTimeoutError),
        ("rate_limit", ProviderRateLimitedError),
        ("unavailable", ProviderUnavailableError),
        ("malformed", ProviderInvalidOutputError),
    ],
)
@pytest.mark.anyio
async def test_fake_transport_has_deterministic_safe_failure_injection(
    failure: str, error_type: type[Exception]
) -> None:
    gateway = StructuredOutputGateway(
        _profile(), FakeStructuredOutputTransport(payload={"answer": "keep"}, failure=failure)
    )

    with pytest.raises(error_type) as error:
        await gateway.call(StructuredOutputRequest("reader_initial_v1", "private prompt"))

    assert "private prompt" not in str(error.value)


@pytest.mark.anyio
async def test_gateway_normalizes_unexpected_or_chained_transport_failures() -> None:
    class UnexpectedTransport:
        async def call(self, _: object) -> object:
            raise RuntimeError("raw provider secret")

    gateway = StructuredOutputGateway(_profile(), UnexpectedTransport())  # type: ignore[arg-type]
    with pytest.raises(ProviderUnavailableError) as unexpected:
        await gateway.call(StructuredOutputRequest("reader_initial_v1", "private prompt"))
    assert unexpected.value.__cause__ is None
    assert "raw provider secret" not in str(unexpected.value)

    class ChainedSafeTransport:
        async def call(self, _: object) -> object:
            try:
                raise RuntimeError("raw provider secret")
            except RuntimeError as error:
                raise ProviderUnavailableError() from error

    gateway = StructuredOutputGateway(_profile(), ChainedSafeTransport())  # type: ignore[arg-type]
    with pytest.raises(ProviderUnavailableError) as chained:
        await gateway.call(StructuredOutputRequest("reader_initial_v1", "private prompt"))
    assert chained.value.__cause__ is None


@pytest.mark.anyio
async def test_gateway_enforces_the_server_owned_timeout() -> None:
    class HangingTransport:
        async def call(self, _: object) -> object:
            await asyncio.sleep(1)
            return object()

    gateway = StructuredOutputGateway(
        _profile(timeout_seconds=0.01), HangingTransport()  # type: ignore[arg-type]
    )
    with pytest.raises(ProviderTimeoutError):
        await gateway.call(StructuredOutputRequest("reader_initial_v1", "private prompt"))


@pytest.mark.anyio
async def test_gateway_normalizes_schema_decode_and_model_validator_exceptions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ExplodingSchemaOutput(BaseModel):
        model_config = ConfigDict(strict=True, extra="forbid")

        answer: str

        @classmethod
        def model_json_schema(cls, *args: object, **kwargs: object) -> dict[str, object]:
            raise RuntimeError("schema-secret")

    schema_gateway = StructuredOutputGateway(
        _profile(output_schema=ExplodingSchemaOutput),
        FakeStructuredOutputTransport(payload={"answer": "keep"}),
    )
    with pytest.raises(ProviderInvalidOutputError) as schema_error:
        await schema_gateway.call(StructuredOutputRequest("reader_initial_v1", "prompt"))
    assert schema_error.value.__cause__ is None
    assert schema_error.value.__context__ is None
    assert "schema-secret" not in repr(schema_error.value)

    monkeypatch.setattr(
        "app.llm.gateway.json.loads",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("decode-secret")),
    )
    decode_gateway = StructuredOutputGateway(
        _profile(), FakeStructuredOutputTransport(payload='{"answer":"keep"}')
    )
    with pytest.raises(ProviderInvalidOutputError) as decode_error:
        await decode_gateway.call(StructuredOutputRequest("reader_initial_v1", "prompt"))
    assert decode_error.value.__cause__ is None
    assert decode_error.value.__context__ is None
    assert "decode-secret" not in repr(decode_error.value)


@pytest.mark.anyio
async def test_gateway_normalizes_arbitrary_model_validator_exception() -> None:
    class ExplodingValidationOutput(BaseModel):
        model_config = ConfigDict(strict=True, extra="forbid")

        answer: str

        @model_validator(mode="before")
        @classmethod
        def explode(cls, value: object) -> object:
            raise RuntimeError("validator-secret")

    gateway = StructuredOutputGateway(
        _profile(output_schema=ExplodingValidationOutput),
        FakeStructuredOutputTransport(payload={"answer": "keep"}),
    )
    with pytest.raises(ProviderInvalidOutputError) as error:
        await gateway.call(StructuredOutputRequest("reader_initial_v1", "prompt"))
    assert error.value.__cause__ is None
    assert error.value.__context__ is None
    assert "validator-secret" not in repr(error.value)


@pytest.mark.anyio
async def test_gateway_does_not_swallow_cancellation() -> None:
    class CancelledTransport:
        async def call(self, _: object) -> object:
            raise asyncio.CancelledError()

    gateway = StructuredOutputGateway(
        _profile(), CancelledTransport()  # type: ignore[arg-type]
    )
    with pytest.raises(asyncio.CancelledError):
        await gateway.call(StructuredOutputRequest("reader_initial_v1", "prompt"))


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
@pytest.mark.anyio
async def test_gateway_rejects_non_finite_json_constants(constant: str) -> None:
    class FloatOutput(BaseModel):
        model_config = ConfigDict(strict=True, extra="forbid")

        score: float

    gateway = StructuredOutputGateway(
        _profile(output_schema=FloatOutput),
        FakeStructuredOutputTransport(payload=f'{{"score":{constant}}}'),
    )
    with pytest.raises(ProviderInvalidOutputError):
        await gateway.call(StructuredOutputRequest("reader_initial_v1", "prompt"))


@pytest.mark.anyio
async def test_gateway_recursively_rejects_overflowed_and_object_non_finite_floats() -> None:
    class NestedFloatOutput(BaseModel):
        model_config = ConfigDict(strict=True, extra="forbid")

        scores: list[float]

    for payload in ('{"scores":[1e10000]}', {"scores": [float("inf")]}):
        gateway = StructuredOutputGateway(
            _profile(output_schema=NestedFloatOutput),
            FakeStructuredOutputTransport(payload=payload),
        )
        with pytest.raises(ProviderInvalidOutputError):
            await gateway.call(StructuredOutputRequest("reader_initial_v1", "prompt"))


def test_profile_recursively_rejects_nested_permissive_models() -> None:
    class PermissiveNested(BaseModel):
        answer: str

    class StrictContainer(BaseModel):
        model_config = ConfigDict(strict=True, extra="forbid")

        nested: list[PermissiveNested] | None

    with pytest.raises(ValueError, match="reachable output models"):
        _profile(output_schema=StrictContainer)


@pytest.mark.anyio
async def test_profile_accepts_recursive_strict_models_and_emits_closed_defs() -> None:
    class StrictNode(BaseModel):
        model_config = ConfigDict(strict=True, extra="forbid")

        label: str
        children: list[StrictNode]

    StrictNode.model_rebuild()
    transport = FakeStructuredOutputTransport(
        payload={"label": "root", "children": [{"label": "leaf", "children": []}]}
    )
    gateway = StructuredOutputGateway(_profile(output_schema=StrictNode), transport)

    await gateway.call(StructuredOutputRequest("reader_initial_v1", "prompt"))

    schema = transport.requests[0].output_schema
    assert schema["$defs"]["StrictNode"]["additionalProperties"] is False  # type: ignore[index]


@pytest.mark.anyio
async def test_gateway_rejects_mapping_object_schema_before_transport() -> None:
    class MappingOutput(BaseModel):
        model_config = ConfigDict(strict=True, extra="forbid")

        values: dict[str, str]

    transport = FakeStructuredOutputTransport(payload={"values": {"key": "value"}})
    gateway = StructuredOutputGateway(_profile(output_schema=MappingOutput), transport)

    with pytest.raises(ProviderInvalidOutputError):
        await gateway.call(StructuredOutputRequest("reader_initial_v1", "prompt"))

    assert transport.requests == []


@pytest.mark.parametrize(
    "field_annotation",
    [
        Any,
        object,
        list[Any],
        list[object],
        tuple[Any, ...],
        str | object,
    ],
)
@pytest.mark.anyio
async def test_gateway_rejects_every_unconstrained_schema_node(
    field_annotation: object,
) -> None:
    UnsafeOutput = type(
        "UnsafeOutput",
        (BaseModel,),
        {
            "__annotations__": {"value": field_annotation},
            "model_config": ConfigDict(strict=True, extra="forbid"),
        },
    )
    transport = FakeStructuredOutputTransport(payload={"value": "unsafe"})
    gateway = StructuredOutputGateway(_profile(output_schema=UnsafeOutput), transport)

    with pytest.raises(ProviderInvalidOutputError):
        await gateway.call(StructuredOutputRequest("reader_initial_v1", "prompt"))

    assert transport.requests == []


@pytest.mark.anyio
async def test_gateway_accepts_explicitly_constrained_schema_node_kinds() -> None:
    class ConstrainedOutput(BaseModel):
        model_config = ConfigDict(strict=True, extra="forbid")

        text: str
        count: int
        score: float
        enabled: bool
        tags: list[str]
        choice: Literal["keep", "revise"]
        optional: str | None

    transport = FakeStructuredOutputTransport(
        payload={
            "text": "safe",
            "count": 1,
            "score": 0.5,
            "enabled": True,
            "tags": ["one"],
            "choice": "keep",
            "optional": None,
        }
    )
    gateway = StructuredOutputGateway(_profile(output_schema=ConstrainedOutput), transport)

    await gateway.call(StructuredOutputRequest("reader_initial_v1", "prompt"))

    assert len(transport.requests) == 1


@pytest.mark.parametrize("json_annotation", [Json[Any], Json[object]])
@pytest.mark.anyio
async def test_gateway_rejects_unconstrained_json_content_schema(
    json_annotation: object,
) -> None:
    UnsafeJsonOutput = type(
        "UnsafeJsonOutput",
        (BaseModel,),
        {
            "__annotations__": {"value": json_annotation},
            "model_config": ConfigDict(strict=True, extra="forbid"),
        },
    )
    transport = FakeStructuredOutputTransport(payload={"value": '{"unsafe":true}'})
    gateway = StructuredOutputGateway(_profile(output_schema=UnsafeJsonOutput), transport)

    with pytest.raises(ProviderInvalidOutputError):
        await gateway.call(StructuredOutputRequest("reader_initial_v1", "prompt"))

    assert transport.requests == []


@pytest.mark.anyio
async def test_gateway_rejects_even_constrained_nested_json_string_schema() -> None:
    class StrictJsonValue(BaseModel):
        model_config = ConfigDict(strict=True, extra="forbid")

        answer: str

    class SafeJsonOutput(BaseModel):
        model_config = ConfigDict(strict=True, extra="forbid")

        value: Json[StrictJsonValue]

    transport = FakeStructuredOutputTransport(payload={"value": '{"answer":"keep"}'})
    gateway = StructuredOutputGateway(_profile(output_schema=SafeJsonOutput), transport)

    with pytest.raises(ProviderInvalidOutputError):
        await gateway.call(StructuredOutputRequest("reader_initial_v1", "prompt"))

    assert transport.requests == []


def test_raw_transport_usage_fields_are_hidden_from_repr() -> None:
    response = StructuredTransportResponse(
        payload="raw-output-secret",
        input_tokens="raw-input-usage-secret",
        output_tokens="raw-output-usage-secret",
    )
    transport = FakeStructuredOutputTransport(
        payload="raw-output-secret",
        input_tokens="raw-input-usage-secret",
        output_tokens="raw-output-usage-secret",
    )

    for value in (response, transport):
        rendered = repr(value)
        assert "raw-output-secret" not in rendered
        assert "raw-input-usage-secret" not in rendered
        assert "raw-output-usage-secret" not in rendered


@pytest.mark.anyio
async def test_gateway_requires_exact_transport_response_and_safe_field_extraction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ResponseSubclass(StructuredTransportResponse):
        pass

    class SubclassTransport:
        async def call(self, _: object) -> StructuredTransportResponse:
            return ResponseSubclass(payload={"answer": "keep"})

    gateway = StructuredOutputGateway(_profile(), SubclassTransport())
    with pytest.raises(ProviderInvalidOutputError):
        await gateway.call(StructuredOutputRequest("reader_initial_v1", "prompt"))

    response = StructuredTransportResponse(payload={"answer": "keep"})

    class ExactTransport:
        async def call(self, _: object) -> StructuredTransportResponse:
            return response

    original_getattribute = StructuredTransportResponse.__getattribute__

    def exploding_getattribute(self: object, name: str) -> object:
        if name == "payload":
            raise RuntimeError("field-secret")
        return original_getattribute(self, name)

    monkeypatch.setattr(StructuredTransportResponse, "__getattribute__", exploding_getattribute)
    gateway = StructuredOutputGateway(_profile(), ExactTransport())
    with pytest.raises(ProviderInvalidOutputError) as error:
        await gateway.call(StructuredOutputRequest("reader_initial_v1", "prompt"))
    assert error.value.__cause__ is None
    assert error.value.__context__ is None
    assert "field-secret" not in repr(error.value)


@pytest.mark.anyio
async def test_gateway_maps_safe_error_subclasses_to_exact_known_category() -> None:
    class UnsafeUnavailable(ProviderUnavailableError):
        def __init__(self, detail: str) -> None:
            self.detail = detail
            super().__init__()

    class UnsafeTransport:
        async def call(self, _: object) -> object:
            raise UnsafeUnavailable("provider-secret")

    gateway = StructuredOutputGateway(_profile(), UnsafeTransport())  # type: ignore[arg-type]
    with pytest.raises(ProviderUnavailableError) as error:
        await gateway.call(StructuredOutputRequest("reader_initial_v1", "prompt"))
    assert type(error.value) is ProviderUnavailableError
    assert error.value.__cause__ is None
    assert error.value.__context__ is None
    assert "provider-secret" not in repr(error.value)


@pytest.mark.anyio
async def test_gateway_normalizer_never_reads_untrusted_exception_class() -> None:
    secret = "hostile-class-secret"

    class HostileUnavailable(ProviderUnavailableError):
        def __getattribute__(self, name: str) -> object:
            if name == "__class__":
                raise RuntimeError(secret)
            return super().__getattribute__(name)

    class HostileTransport:
        async def call(self, _: object) -> object:
            raise HostileUnavailable()

    gateway = StructuredOutputGateway(_profile(), HostileTransport())  # type: ignore[arg-type]
    with pytest.raises(ProviderUnavailableError) as error:
        await gateway.call(StructuredOutputRequest("reader_initial_v1", "prompt"))

    assert type(error.value) is ProviderUnavailableError
    assert error.value.__cause__ is None
    assert error.value.__context__ is None
    assert secret not in repr(error.value)


def test_profile_rejects_non_strict_schema_and_unsafe_server_owned_values() -> None:
    class PermissiveOutput(BaseModel):
        answer: str

    with pytest.raises(ValueError):
        _profile(output_schema=PermissiveOutput)
    with pytest.raises(ValueError):
        _profile(model_identifier="https://client-selected.example/model")
    with pytest.raises(ValueError):
        _profile(timeout_seconds=121)


@pytest.mark.parametrize(
    "overrides",
    [
        {"profile_id": "reader_sk-prod"},
        {"provider_kind": "fake_sk-prod"},
        {"model_identifier": "vendor/sk-proj-safe-looking"},
        {"prompt_template_version": "v1_sk-prod"},
        {"output_schema_name": "output_token-secret"},
    ],
)
def test_profile_rejects_sensitive_material_in_any_identifier_segment(
    overrides: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        _profile(**overrides)
