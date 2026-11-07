def in_range(value: int, lower: int, upper: int) -> bool:
    """Check inclusive integer bounds without silently reversing them.

    The caller supplies integers. Equal lower and upper bounds are allowed.
    Invalid bounds raise ValueError. Both endpoints are included in the range.
    Examples: in_range(3, 1, 3) is True; in_range(4, 1, 3) is False.
    """
    if lower > upper:
        raise ValueError("lower must not exceed upper")
    return lower <= value <= upper
