class AppError(Exception):
    def __init__(self, status: int, code: str, message: str, extra: dict | None = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.extra = extra or {}


class NotFound(AppError):
    def __init__(self, message="资源不存在", code="NOT_FOUND", **extra):
        super().__init__(404, code, message, extra)


class Conflict(AppError):
    def __init__(self, code, message, **extra):
        super().__init__(409, code, message, extra)


class Invalid(AppError):
    def __init__(self, code, message, **extra):
        super().__init__(422, code, message, extra)


class BadNumber(ValueError):
    def __init__(self):
        super().__init__("必须是合法数字")


class TooManyDecimals(ValueError):
    def __init__(self):
        super().__init__("最多两位小数")
