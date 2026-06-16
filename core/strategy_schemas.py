from __future__ import annotations

from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrategyParamsBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    def to_payload(self) -> dict[str, Any]:
        return self.model_dump(exclude_none=True)


class EmptyStrategyParams(StrategyParamsBase):
    pass


class EmaCrossoverParams(StrategyParamsBase):
    short: int = Field(..., ge=1)
    long: int = Field(..., ge=2)

    @model_validator(mode="after")
    def validate_lengths(self) -> "EmaCrossoverParams":
        if self.long <= self.short:
            raise ValueError("'long' must be greater than 'short'")
        return self


class VolumeSpikeParams(StrategyParamsBase):
    volume_spike_mult: float = Field(1.5, gt=0.0)
    volume_spike_lookback: int = Field(20, ge=1)


class EmaCrossoverV2Params(EmaCrossoverParams):
    volume_spike_mult: float = Field(1.5, gt=0.0)
    volume_spike_lookback: int = Field(20, ge=1)


class DonchianBreakoutParams(StrategyParamsBase):
    lookback: int = Field(..., ge=2)


class DonchianBreakoutV2Params(DonchianBreakoutParams):
    volume_spike_mult: float = Field(1.5, gt=0.0)
    volume_spike_lookback: int = Field(20, ge=1)


class ThreeCandleReversalParams(StrategyParamsBase):
    min_body_ratio: float = Field(0.55, gt=0.0, le=1.0)
    require_full_range_engulf: bool = True
    confirm_break_prev_extreme: bool = True


class ThreeCandleReversalV2Params(ThreeCandleReversalParams):
    volume_spike_mult: float = Field(1.5, gt=0.0)
    volume_spike_lookback: int = Field(20, ge=1)


class BollingerMeanReversionParams(StrategyParamsBase):
    # Canonical public/API names. UI should emit these names only.
    length: int = Field(20, ge=2)
    std_dev: float = Field(2.0, gt=0.0)
    exit_on_mid: bool = True
    min_band_width_pct: float = Field(0.01, ge=0.0)
    allow_short: bool = False

    @model_validator(mode="before")
    @classmethod
    def alias_inputs(cls, data: Any) -> Any:
        """Accept old saved-config aliases, but return canonical names."""
        if not isinstance(data, dict):
            return data

        out = dict(data)

        alias_map = {
            "window": "length",
            "period": "length",
            "lookback": "length",
            "stddev": "std_dev",
            "stdev": "std_dev",
            "std": "std_dev",
            "std_mult": "std_dev",
            "min_bandwidth_pct": "min_band_width_pct",
            "min_bandwidth": "min_band_width_pct",
            "min_band_width": "min_band_width_pct",
        }

        for old_key, new_key in alias_map.items():
            if old_key in out and new_key not in out:
                out[new_key] = out.pop(old_key)

        return out


class BollingerMeanReversionV2Params(BollingerMeanReversionParams):
    volume_spike_mult: float = Field(1.5, gt=0.0)
    volume_spike_lookback: int = Field(20, ge=1)


class AtrRiskParams(StrategyParamsBase):
    atr_length: int = Field(14, ge=1)
    stop_atr_mult: float = Field(..., gt=0.0)
    take_profit_atr_mult: float = Field(..., gt=0.0)


class StrategyDefinition(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str
    params_model: type[StrategyParamsBase]
    aliases: tuple[str, ...] = ()


class StrategyRegistry:
    _definitions: ClassVar[dict[str, StrategyDefinition]] = {}

    @classmethod
    def register(
        cls,
        name: str,
        params_model: type[StrategyParamsBase],
        *,
        aliases: tuple[str, ...] = (),
    ) -> None:
        key = name.strip().lower()
        definition = StrategyDefinition(
            name=key,
            params_model=params_model,
            aliases=aliases,
        )
        cls._definitions[key] = definition

        for alias in aliases:
            cls._definitions[alias.strip().lower()] = definition

    @classmethod
    def get(cls, strategy_name: str | None) -> StrategyDefinition | None:
        if not strategy_name:
            return None
        return cls._definitions.get(strategy_name.strip().lower())

    @classmethod
    def canonical_name(cls, strategy_name: str | None) -> str | None:
        definition = cls.get(strategy_name)
        return definition.name if definition else None

    @classmethod
    def validate(
        cls,
        strategy_name: str | None,
        strategy_params: dict[str, Any] | None,
    ) -> tuple[str | None, dict[str, Any] | None]:
        if not strategy_name:
            return None, None

        definition = cls.get(strategy_name)
        if definition is None:
            supported = sorted({d.name for d in cls._definitions.values()})
            raise ValueError(
                f"Unsupported strategy_name '{strategy_name}'. "
                f"Supported strategies: {', '.join(supported)}"
            )

        raw_params = strategy_params or {}
        validated = definition.params_model.model_validate(raw_params)
        return definition.name, validated.to_payload()

    @classmethod
    def supported_strategies(cls) -> list[str]:
        return sorted({d.name for d in cls._definitions.values()})


StrategyRegistry.register(
    "ema_crossover",
    EmaCrossoverParams,
    aliases=("ema",),
)

StrategyRegistry.register(
    "ema_crossover_v2",
    EmaCrossoverV2Params,
    aliases=("ema_v2",),
)

StrategyRegistry.register(
    "donchian_breakout",
    DonchianBreakoutParams,
    aliases=("donchian",),
)

StrategyRegistry.register(
    "donchian_breakout_v2",
    DonchianBreakoutV2Params,
    aliases=("donchian_v2",),
)

StrategyRegistry.register(
    "three_candle_reversal",
    ThreeCandleReversalParams,
    aliases=("3cr",),
)

StrategyRegistry.register(
    "three_candle_reversal_v2",
    ThreeCandleReversalV2Params,
    aliases=("3cr_v2",),
)

StrategyRegistry.register(
    "bollinger_mean_reversion",
    BollingerMeanReversionParams,
    aliases=("bb_mean_reversion", "bollinger", "bbmr"),
)

StrategyRegistry.register(
    "bollinger_mean_reversion_v2",
    BollingerMeanReversionV2Params,
    aliases=("bb_mean_reversion_v2", "bollinger_v2", "bbmr_v2"),
)
