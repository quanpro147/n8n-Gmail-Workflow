class ApiError(Exception):
    """An error that maps directly to the JSON error envelope returned to n8n.

    `retryable` tells the caller whether repeating the same request may succeed.
    """

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        message_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.retryable = retryable
        self.message_id = message_id

    def to_body(self) -> dict:
        error = {"code": self.code, "message": self.message, "retryable": self.retryable}
        if self.message_id is not None:
            error["message_id"] = self.message_id
        return {"error": error}
