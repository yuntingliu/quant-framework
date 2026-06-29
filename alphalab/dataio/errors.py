"""Data-layer exceptions."""


class DataLoadError(RuntimeError):
    """Raised when a provider cannot load data."""


class DataValidationError(ValueError):
    """Raised when loaded data has an invalid schema."""


class MissingDataError(DataLoadError):
    """Raised when requested data is unavailable."""

