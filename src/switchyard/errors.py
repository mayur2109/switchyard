"""Stable, content-free operational errors."""


class DecisionError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")

    def as_dict(self):
        return {"code": self.code, "message": self.message}
