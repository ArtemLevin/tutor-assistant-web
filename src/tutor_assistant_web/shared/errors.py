class ApplicationError(RuntimeError):
    status_code = 400


class NotFoundError(ApplicationError):
    status_code = 404


class ConflictError(ApplicationError):
    status_code = 409


class ValidationError(ApplicationError):
    status_code = 422


class GoneError(ApplicationError):
    status_code = 410
