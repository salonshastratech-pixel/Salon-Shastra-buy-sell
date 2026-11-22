class ValidationError(Exception):
    """Business-rule violation with field-level messages for the UI."""

    def __init__(self, errors: dict | str):
        if isinstance(errors, str):
            errors = {"_": errors}
        self.errors = errors
        super().__init__("; ".join(f"{k}: {v}" for k, v in errors.items()))


class PermissionDenied(Exception):
    def __init__(self, capability: str):
        self.capability = capability
        super().__init__(capability)
