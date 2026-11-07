def is_even(number: int) -> bool:
    """Report whether an integer is divisible by two.

    Zero and negative even integers are even. Strings and booleans are
    rejected with TypeError rather than being silently converted.
    Examples: is_even(0) is True; is_even(-3) is False.
    """
    if isinstance(number, bool) or not isinstance(number, int):
        raise TypeError("number must be an integer")
    return number % 2 == 0
