def clamp(value: int, lower: int, upper: int) -> int:
    """Limit an integer to the inclusive interval from lower to upper.

    The caller supplies integer arguments. Invalid bounds raise ValueError.
    A value already inside the interval is returned unchanged.
    Examples: clamp(8, 1, 5) returns 5; clamp(-2, 0, 10) returns 0.
    """
    if lower > upper:
        raise ValueError("lower must not exceed upper")
    if value < lower:
        return lower
    if value > upper:
        return upper
    return value
