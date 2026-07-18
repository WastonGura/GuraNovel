"""Safe, provider-neutral failures for chapter generation."""

from fastapi import status

from app.core.errors import AgentOutputInvalidError, AppError


class ProviderUnavailableError(AppError):
    """Raised when a generation provider cannot currently serve a request."""

    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    code = "provider_unavailable"
    default_message = "The generation provider is temporarily unavailable. Please try again later."

    def __init__(self) -> None:
        super().__init__()


class ProviderTimeoutError(AppError):
    """Raised when a generation provider does not respond before its deadline."""

    status_code = status.HTTP_504_GATEWAY_TIMEOUT
    code = "provider_timeout"
    default_message = "The generation provider timed out. Please try again later."

    def __init__(self) -> None:
        super().__init__()


class ProviderRateLimitedError(AppError):
    """Raised when a generation provider rejects a request due to rate limiting."""

    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    code = "provider_rate_limited"
    default_message = "The generation provider is rate limited. Please try again later."

    def __init__(self) -> None:
        super().__init__()


class ProviderInvalidOutputError(AgentOutputInvalidError):
    """Raised when a generation provider returns unusable chapter artifacts."""

    code = "provider_invalid_output"
    default_message = "The generation provider returned invalid output."

    def __init__(self) -> None:
        super().__init__()


class ProviderConfigurationError(AppError):
    """Raised when the selected provider lacks safe server configuration."""

    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    code = "provider_configuration_error"
    default_message = "The generation provider is not configured. Please contact the service operator."

    def __init__(self) -> None:
        super().__init__()
