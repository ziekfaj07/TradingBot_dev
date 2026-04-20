class ExchangeAdapterError(RuntimeError):
    pass


class ExchangeConfigurationError(ExchangeAdapterError):
    pass


class ExchangeConnectionError(ExchangeAdapterError):
    pass


class ExchangeAuthError(ExchangeAdapterError):
    pass

class ExchangeOrderRejected(ExchangeAdapterError):
    pass


class ExchangeTimeoutError(ExchangeAdapterError):
    pass


class ExchangeRateLimitError(ExchangeAdapterError):
    pass
