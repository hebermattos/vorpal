def validate_port(port: int) -> int:
    """Return a valid TCP/UDP port or raise an explicit error.

    Integers from 1 through 65535 are valid. Booleans are rejected even
    though Python considers bool a subclass of int. No network access occurs.
    Examples: validate_port(443) returns 443; validate_port(0) raises ValueError.
    """
    if isinstance(port, bool) or not isinstance(port, int):
        raise TypeError("port must be an integer")
    if not 1 <= port <= 65535:
        raise ValueError("port must be between 1 and 65535")
    return port
