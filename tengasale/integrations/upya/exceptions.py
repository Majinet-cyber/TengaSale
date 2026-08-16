class UpyaError(Exception):
    """Base exception for the Upya integration boundary."""


class UpyaConfigurationError(UpyaError):
    pass


class UpyaAuthenticationError(UpyaError):
    pass


class UpyaTimeout(UpyaError):
    pass


class UpyaBadResponse(UpyaError):
    pass


class UpyaCustomerNotFound(UpyaError):
    pass
