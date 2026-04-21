class ExchangeAdapterError(RuntimeError):
    pass


class ExchangeConfigurationError(ExchangeAdapterError):
    pass


class ExchangeConnectionError(ExchangeAdapterError):
    pass


class ExchangeAuthError(ExchangeAdapterError):
    pass