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
