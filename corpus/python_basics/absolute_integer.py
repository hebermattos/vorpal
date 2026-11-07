def absolute_integer(number: int) -> int:
    """Return the nonnegative magnitude of an integer.

    This exercise demonstrates input validation followed by a conditional
    return. Booleans are deliberately rejected to keep the interface explicit.
    Examples: absolute_integer(-5) returns 5; absolute_integer(0) returns 0.
    """
    if isinstance(number, bool) or not isinstance(number, int):
        raise TypeError("number must be an integer")
    if number < 0:
        return -number
    return number
