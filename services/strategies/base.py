from __future__ import annotations

from abc import ABC, abstractmethod
import pandas as pd


class BaseStrategy(ABC):
    name: str = "base"

    @abstractmethod
    def apply(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Input: OHLCV dataframe
        Output: dataframe with at least a `signal` column
        signal semantics:
          1 = enter long
         -1 = exit long
          0 = hold
        """
        raise NotImplementedError