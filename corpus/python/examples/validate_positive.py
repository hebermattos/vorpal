def validate_positive(number: int) -> int:
    """Return a positive integer, rejecting other inputs explicitly.

    Booleans and strings raise TypeError. Zero and negative integers raise
    ValueError. This function performs validation without changing the input.
    Examples: validate_positive(7) returns 7; validate_positive(0) raises ValueError.
    """
    if isinstance(number, bool) or not isinstance(number, int):
        raise TypeError("number must be an integer")
    if number <= 0:
        raise ValueError("number must be positive")
    return number
