class DomainError(Exception):
    code = "DOMAIN_ERROR"
    status_code = 400

    def __init__(self, message: str, details: dict | None = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}


class NotFound(DomainError):
    code, status_code = "NOT_FOUND", 404


class Unauthorized(DomainError):
    code, status_code = "UNAUTHORIZED", 401


class Forbidden(DomainError):
    code, status_code = "FORBIDDEN", 403


class Conflict(DomainError):
    code, status_code = "CONFLICT", 409


class ValidationFailed(DomainError):
    code, status_code = "VALIDATION_ERROR", 422


class StorageUnavailable(DomainError):
    code, status_code = "STORAGE_UNAVAILABLE", 503


class AgentUnavailable(DomainError):
    code, status_code = "AGENT_UNAVAILABLE", 503


class GenerationRejected(DomainError):
    """The Agent could not produce a usable scenario; `code` carries the Agent's reason."""

    code, status_code = "GENERATION_REJECTED", 422

    def __init__(self, message: str, details: dict | None = None, code: str | None = None):
        super().__init__(message, details)
        if code:
            self.code = code


class CatalogUnavailable(DomainError):
    """No CARLA catalog snapshot can serve the request (none installed or not synced yet)."""

    code, status_code = "CATALOG_NOT_SYNCED", 409

    def __init__(self, message: str, code: str | None = None):
        super().__init__(message)
        if code:
            self.code = code
