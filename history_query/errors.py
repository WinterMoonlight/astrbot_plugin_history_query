"""Public failures contain no SQL, credentials, paths, or message bodies."""


class QueryError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def fail(code: str, message: str):
    raise QueryError(code, message)
