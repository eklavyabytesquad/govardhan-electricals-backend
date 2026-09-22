class ApiError(Exception):
    """Raised by services; the HTTP layer turns this into a JSON error response."""

    def __init__(self, status, message):
        super().__init__(message)
        self.status, self.message = status, message
