"""Custom exception hierarchy for HTS operations."""


class HTSError(Exception):
    """Base exception for all HTS client and communication errors."""
    pass


class HTSConnectionError(HTSError):
    """Raised when an HTTP connection failure, network error, or timeout occurs."""
    pass


class HTSSessionExpiredError(HTSError):
    """Raised when a request detects that the HTS user session is invalid or expired."""
    pass


class HTSParseError(HTSError):
    """Raised when parsing HTS HTML or JSON response fails due to unexpected format."""
    pass
