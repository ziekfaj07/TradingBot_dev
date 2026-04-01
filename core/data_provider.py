from abc import ABC, abstractmethod

import pandas as pd


class DataProvider(ABC):
    @abstractmethod
    def load_ohlcv(
        self,
        symbol: str,
        interval: str,
        market_type: str,
        start: str | None = None,
        end: str | None = None,
    ) -> pd.DataFrame:
        """Return normalized OHLCV dataframe."""

    @abstractmethod
    def export_to_csv(
        self,
        symbol: str,
        interval: str,
        market_type: str,
        out_path: str | None = None,
        start: str | None = None,
        end: str | None = None,
    ) -> str:
        """Export to CSV and return filepath."""