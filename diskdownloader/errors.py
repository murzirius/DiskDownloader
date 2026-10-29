class AppError(Exception):
    """Only a translation key and safe parameters cross the HTTP boundary."""
    def __init__(self, code, *, retryable=False, source="application"):
        super().__init__(code)
        self.code = code
        self.retryable = retryable
        self.source = source


class Interrupted(Exception):
    pass
