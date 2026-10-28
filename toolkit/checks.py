"""Input validation. Analyst rule: a bad input must fail loudly, never silently."""


def require_positive(name, value):
    if value is None:
        raise ValueError(f"{name} is required")
    if value <= 0:
        raise ValueError(f"{name} must be positive, got {value}")
    return value


def require_non_negative(name, value):
    if value is None:
        raise ValueError(f"{name} is required")
    if value < 0:
        raise ValueError(f"{name} must not be negative, got {value}")
    return value


def require_rate(name, value):
    if value is None:
        raise ValueError(f"{name} is required")
    if not -1 < value < 1:
        raise ValueError(f"{name} must be between -1 and 1, got {value}")
    return value
