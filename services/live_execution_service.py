from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from core.execution_models import Fill
from market.exceptions import ExchangeAdapterError, ExchangeConfigurationError
from services.exchange_service import build_exchange_adapter, resolve_exchange_env_names


@dataclass(slots=True)
class LiveOrderResult:
    ok: bool
    order: dict[str, Any]
    status: str
    dry_run: bool
    armed: bool
    submitted_at: str | None = None
    acknowledged_at: str | None = None
    filled_at: str | None = None    

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "order": self.order,
            "status": self.status,
            "dry_run": self.dry_run,
            "armed": self.armed,
            "submitted_at": self.submitted_at,
            "acknowledged_at": self.acknowledged_at,
            "filled_at": self.filled_at,            
        }


class LiveExecutionService:
    def __init__(
        self,
        *,
        exchange_name: str,
        market_type: str,
        testnet: bool,
        settle_currency: str,
        api_key_env: str,
        api_secret_env: str,
        api_passphrase_env: str | None,
        live_api_key_env: str | None = None,
        live_api_secret_env: str | None = None,
        live_api_passphrase_env: str | None = None,
        demo_api_key_env: str | None = None,
        demo_api_secret_env: str | None = None,
        demo_api_passphrase_env: str | None = None,
        client_order_id_prefix: str = "tb",
        armed: bool = False,
        dry_run: bool = True,
    ) -> None:
        self.exchange_name = str(exchange_name or "gateio").strip().lower()
        self.market_type = str(market_type or "spot").strip().lower()
        self.testnet = bool(testnet)
        self.settle_currency = str(settle_currency or "USDT").strip().upper() or "USDT"
        self.client_order_id_prefix = self._normalize_client_order_id_prefix(client_order_id_prefix)
        self.armed = bool(armed)
        self.dry_run = bool(dry_run)

        resolved = resolve_exchange_env_names(
            testnet=self.testnet,
            api_key_env=api_key_env,
            api_secret_env=api_secret_env,
            api_passphrase_env=api_passphrase_env,
            live_api_key_env=live_api_key_env,
            live_api_secret_env=live_api_secret_env,
            live_api_passphrase_env=live_api_passphrase_env,
            demo_api_key_env=demo_api_key_env,
            demo_api_secret_env=demo_api_secret_env,
            demo_api_passphrase_env=demo_api_passphrase_env,
        )
        self.credential_profile = str(resolved["credential_profile"])
        self.resolved_api_key_env = str(resolved["api_key_env"] or "")
        self.resolved_api_secret_env = str(resolved["api_secret_env"] or "")
        self.resolved_api_passphrase_env = resolved["api_passphrase_env"]

        self.adapter = build_exchange_adapter(
            exchange_name=self.exchange_name,
            market_type=self.market_type,
            testnet=self.testnet,
            settle_currency=self.settle_currency,
            api_key_env=self.resolved_api_key_env,
            api_secret_env=self.resolved_api_secret_env,
            api_passphrase_env=self.resolved_api_passphrase_env,
        )

        self.last_risk_preflight: dict[str, Any] | None = None        

    def _should_retry_live_public(self, exc: Exception) -> bool:
        return (
            self.exchange_name == "gateio"
            and bool(self.testnet)
            and "INTERNAL" in str(exc).upper()
        )

    def _fetch_live_public_ticker(self, symbol: str) -> dict[str, Any]:
        public_adapter = build_exchange_adapter(
            exchange_name=self.exchange_name,
            market_type=self.market_type,
            testnet=False,
            api_key_env="__unused__",
            api_secret_env="__unused__",
            api_passphrase_env=None,
        )
        return public_adapter.fetch_ticker(symbol)

    @property
    def can_submit_live_orders(self) -> bool:
        return self.armed and not self.dry_run

    def sync_account(
            self,
            *,
            symbol: str,
            include_recent_trades: bool = False,
            trades_since_ms: int | None = None,
            trades_limit: int = 100,
    ) -> dict[str, Any]:
        normalized_symbol = self.adapter.normalize_symbol(symbol)
        warnings: list[str] = []

        try:
            ticker = self.adapter.fetch_ticker(normalized_symbol)
        except Exception as exc:
            if not self._should_retry_live_public(exc):
                raise
            ticker = self._fetch_live_public_ticker(normalized_symbol)
            warnings.append(
                "Gate.io demo public ticker returned an internal error; "
                "ticker was retried against the live public endpoint."
            )

        balance = self.adapter.fetch_balance()
        try:
            open_orders = self.adapter.fetch_open_orders(normalized_symbol)
        except Exception as exc:
            if not self._should_retry_live_public(exc):
                raise
            open_orders = []
            warnings.append(
                "Gate.io demo open-orders lookup returned an internal error; "
                "continuing with an empty open-order snapshot."
            )
        positions: list[dict[str, Any]] = []

        recent_trades: list[dict[str, Any]] = []
        if include_recent_trades:
            try:
                recent_trades = self.fetch_recent_trades(
                    symbol=normalized_symbol,
                    since_ms=trades_since_ms,
                    limit=trades_limit,
                )
            except Exception:
                recent_trades = []

        if self.market_type == "swap":
            try:
                positions = self.adapter.fetch_positions(normalized_symbol)
            except Exception as exc:
                if not self._should_retry_live_public(exc):
                    raise
                positions = []
                warnings.append(
                    "Gate.io demo positions lookup returned an internal error; "
                    "continuing with an empty position snapshot."
                )
        return {
            "exchange": self.exchange_name,
            "market_type": self.market_type,
            "symbol": normalized_symbol,
            "environment": self.adapter.describe().get("environment"),
            "settle_currency": self.settle_currency,
            "credential_profile": self.credential_profile,
            "resolved_api_key_env": self.resolved_api_key_env,
            "resolved_api_secret_env": self.resolved_api_secret_env,
            "position_mode": self.get_position_mode(),
            "ticker": self.normalize_ticker(ticker),
            "balance": self.normalize_balance(balance),
            "open_orders": [self.normalize_order(x) for x in open_orders],
            "positions": [self.normalize_position(x) for x in positions],
            "synced_at": datetime.now(timezone.utc).isoformat(),
            "armed": self.armed,
            "dry_run": self.dry_run,
            "recent_trades": recent_trades,            
            "warnings": warnings,
        }

    def fetch_recent_trades(
        self,
        *,
        symbol: str,
        since_ms: int | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        normalized_symbol = self.adapter.normalize_symbol(symbol)
        rows = self.adapter.fetch_my_trades(normalized_symbol, since=since_ms, limit=limit)
        return [self.normalize_trade(x) for x in rows]

    def get_position_mode(self) -> str | None:
        try:
            return self.adapter.get_position_mode()
        except Exception:
            return None

    @staticmethod
    def _float_or_none(value: Any) -> float | None:
        try:
            if value is None or value == "":
                return None
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _normalize_margin_mode(value: Any) -> str | None:
        text = str(value or "").strip().lower()
        if text in {"cross", "isolated"}:
            return text
        return None

    def _extract_margin_mode(self, payload: dict[str, Any] | None) -> str | None:
        raw = dict(payload or {})
        info = raw.get("info") if isinstance(raw.get("info"), dict) else {}
        for candidate in (
            raw.get("marginMode"),
            raw.get("margin_mode"),
            info.get("margin_mode"),
            info.get("marginMode"),
            info.get("pos_margin_mode"),
        ):
            normalized = self._normalize_margin_mode(candidate)
            if normalized:
                return normalized
        for candidate in (raw.get("cross_leverage_limit"), info.get("cross_leverage_limit")):
            cross_limit = self._float_or_none(candidate)
            if cross_limit is not None and cross_limit > 0.0:
                return "cross"
        return None

    def _extract_leverage(self, payload: dict[str, Any] | None) -> float | None:
        raw = dict(payload or {})
        info = raw.get("info") if isinstance(raw.get("info"), dict) else {}
        for candidate in (
            raw.get("leverage"),
            raw.get("lever"),
            raw.get("longLeverage"),
            raw.get("shortLeverage"),
            raw.get("cross_leverage_limit"),
            info.get("lever"),
            info.get("leverage"),
            info.get("long_leverage"),
            info.get("short_leverage"),
            info.get("cross_leverage_limit"),
        ):
            lev = self._float_or_none(candidate)
            if lev is not None and lev > 0.0:
                return float(lev)
        return None

    def prepare_entry_risk_settings(
        self,
        *,
        symbol: str,
        leverage: float | None,
        margin_mode: str | None,
    ) -> dict[str, Any]:
        normalized_symbol = self.adapter.normalize_symbol(symbol)
        configured_margin_mode = self._normalize_margin_mode(margin_mode) or "cross"
        configured_leverage = self._float_or_none(leverage)
        if self.market_type != "swap" or configured_leverage is None:
            result = {
                "symbol": normalized_symbol,
                "market_type": self.market_type,
                "configured_margin_mode": configured_margin_mode,
                "configured_leverage": configured_leverage,
                "exchange_margin_mode": None,
                "exchange_leverage": None,
                "margin_mode_match": None,
                "leverage_match": None,
                "verified": False,
                "source": None,
            }
            self.last_risk_preflight = result
            return result

        configured_leverage = max(1.0, float(configured_leverage))
        margin_result = self.adapter.set_margin_mode(
            margin_mode=configured_margin_mode,
            symbol=normalized_symbol,
            leverage=configured_leverage,
        )
        leverage_result = self.adapter.set_leverage(
            leverage=configured_leverage,
            symbol=normalized_symbol,
            margin_mode=configured_margin_mode,
        )

        try:
            verified = self.adapter.fetch_effective_leverage(
                symbol=normalized_symbol,
                margin_mode=configured_margin_mode,
            )
        except ExchangeAdapterError:
            verified = dict(leverage_result or {})
            verified.setdefault("source", "set_leverage")
        exchange_margin_mode = self._extract_margin_mode(verified) or self._normalize_margin_mode(verified.get("margin_mode"))
        exchange_leverage = self._extract_leverage(verified) or self._float_or_none(verified.get("leverage"))
        if exchange_margin_mode is None:
            exchange_margin_mode = self._extract_margin_mode(margin_result)
        if exchange_leverage is None:
            exchange_leverage = self._extract_leverage(leverage_result)
        margin_mode_match = exchange_margin_mode == configured_margin_mode if exchange_margin_mode is not None else None
        leverage_match = (
            exchange_leverage is not None and abs(float(exchange_leverage) - float(configured_leverage)) <= 1e-9
        )

        result = {
            "symbol": normalized_symbol,
            "market_type": self.market_type,
            "configured_margin_mode": configured_margin_mode,
            "configured_leverage": float(configured_leverage),
            "exchange_margin_mode": exchange_margin_mode,
            "exchange_leverage": exchange_leverage,
            "margin_mode_match": margin_mode_match,
            "leverage_match": leverage_match,
            "verified": bool(margin_mode_match and leverage_match),
            "source": verified.get("source") if isinstance(verified, dict) else None,
            "raw": verified.get("raw") if isinstance(verified, dict) else verified,
            "margin_set_result": margin_result,
            "leverage_set_result": leverage_result,
        }
        self.last_risk_preflight = result
        if not result["verified"]:
            raise ExchangeConfigurationError(
                "Live leverage verification failed for "
                f"{normalized_symbol}: configured margin_mode={configured_margin_mode}, leverage={configured_leverage}, "
                f"exchange margin_mode={exchange_margin_mode}, leverage={exchange_leverage}"
            )
        return result

    @staticmethod
    def _to_epoch_ms(value: Any) -> int | None:
        if value is None or value == "":
            return None

        if isinstance(value, (int, float)):
            raw = float(value)
            if raw <= 0:
                return None
            # seconds vs milliseconds
            if raw < 10_000_000_000:
                raw *= 1000.0
            return int(raw)

        text = str(value).strip()
        if not text:
            return None

        try:
            as_num = float(text)
            if as_num > 0:
                if as_num < 10_000_000_000:
                    as_num *= 1000.0
                return int(as_num)
        except ValueError:
            pass

        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
            return int(dt.timestamp() * 1000)
        except Exception:
            return None

    @classmethod
    def _iso_from_any(cls, value: Any) -> str | None:
        epoch_ms = cls._to_epoch_ms(value)
        if epoch_ms is None:
            return None
        return datetime.fromtimestamp(epoch_ms / 1000.0, tz=timezone.utc).isoformat()

    @classmethod
    def _latency_ms(cls, start_value: Any, end_value: Any) -> float | None:
        start_ms = cls._to_epoch_ms(start_value)
        end_ms = cls._to_epoch_ms(end_value)
        if start_ms is None or end_ms is None:
            return None
        return float(max(0, end_ms - start_ms))

    def _extract_acknowledged_at(self, payload: dict[str, Any]) -> str | None:
        raw = dict(payload or {})
        info = raw.get("info") if isinstance(raw.get("info"), dict) else {}

        candidates = [
            raw.get("datetime"),
            raw.get("timestamp"),
            raw.get("createdAt"),
            raw.get("create_time"),
            raw.get("createTime"),
            raw.get("updateTime"),
            info.get("create_time_ms"),
            info.get("create_time"),
            info.get("update_time_ms"),
            info.get("update_time"),
        ]
        for item in candidates:
            iso = self._iso_from_any(item)
            if iso:
                return iso
        return None

    def _extract_filled_at(self, payload: dict[str, Any]) -> str | None:
        raw = dict(payload or {})
        info = raw.get("info") if isinstance(raw.get("info"), dict) else {}

        candidates = [
            raw.get("lastTradeTimestamp"),
            raw.get("lastFillTime"),
            raw.get("filledAt"),
            raw.get("fillTime"),
            raw.get("tradeTime"),
            raw.get("updateTime"),
            info.get("finish_time_ms"),
            info.get("finish_time"),
            info.get("fill_time_ms"),
            info.get("fill_time"),
            info.get("update_time_ms"),
            info.get("update_time"),
        ]
        for item in candidates:
            iso = self._iso_from_any(item)
            if iso:
                return iso
        return None

    @staticmethod
    def _normalize_client_order_id_prefix(prefix: str | None) -> str:
        raw = str(prefix or "tb").strip()
        if not raw:
            raw = "tb"
        if raw.startswith("t-"):
            return raw
        raw = raw.lstrip("-")
        return f"t-{raw}"

    def _contract_size_for_symbol(self, symbol: str) -> float | None:
        try:
            return self.adapter.contract_size_for_symbol(symbol)
        except Exception:
            return None

    def _contracts_from_base_qty(self, *, symbol: str, base_qty: float) -> tuple[float, float | None]:
        contract_size = self._contract_size_for_symbol(symbol)
        qty = float(base_qty)
        if self.market_type != "swap":
            return qty, contract_size
        if contract_size is None or contract_size <= 0:
            return qty, contract_size
        # Futures orders are submitted in whole contracts on Gate.io.
        # Do not round up above the risk engine's requested base quantity;
        # use the largest whole-contract amount that does not exceed it.
        contracts = max(1.0, float(int(qty / contract_size)))
        return float(contracts), float(contract_size)

    def _resolve_contract_size(
        self,
        *,
        symbol: Any = None,
        payload: dict[str, Any] | None = None,
        info: dict[str, Any] | None = None,
    ) -> float | None:
        payload = dict(payload or {})
        info = dict(info or {})
        for candidate in (
            payload.get("contractSize"),
            payload.get("contract_size"),
            info.get("quanto_multiplier"),
            info.get("contract_size"),
        ):
            value = self._float_or_none(candidate)
            if value is not None and value > 0:
                return value
        symbol_value = symbol or payload.get("symbol")
        if symbol_value:
            return self._contract_size_for_symbol(str(symbol_value))
        return None

    def submit_order(
        self,
        *,
        symbol: str,
        side: str,
        qty: float,
        order_type: str = "market",
        price: float | None = None,
        reduce_only: bool = False,
        client_order_id: str | None = None,
        leverage: float | None = None,
        margin_mode: str | None = None,
    ) -> LiveOrderResult:
        normalized_symbol = self.adapter.normalize_symbol(symbol)
        normalized_side = str(side or "buy").strip().lower()
        normalized_type = str(order_type or "market").strip().lower()
        requested_base_qty = float(qty)
        if requested_base_qty <= 0.0:
            raise ExchangeConfigurationError("Order qty must be > 0.")

        submitted_at = datetime.now(timezone.utc).isoformat()
        exchange_amount, contract_size = self._contracts_from_base_qty(
            symbol=normalized_symbol,
            base_qty=requested_base_qty,
        )

        params: dict[str, Any] = {}
        if client_order_id:
            params["text"] = str(client_order_id)
        if reduce_only and self.market_type == "swap":
            params["reduce_only"] = True

        if not self.can_submit_live_orders:
            self.last_risk_preflight = {
                "symbol": normalized_symbol,
                "market_type": self.market_type,
                "configured_margin_mode": self._normalize_margin_mode(margin_mode),
                "configured_leverage": self._float_or_none(leverage),
                "exchange_margin_mode": None,
                "exchange_leverage": None,
                "margin_mode_match": None,
                "leverage_match": None,
                "verified": False,
                "source": "dry_run",
            }

            fake_now = datetime.now(timezone.utc).isoformat()
            fake_order = {
                "id": f"dryrun-{int(datetime.now(timezone.utc).timestamp() * 1000)}",
                "clientOrderId": client_order_id,
                "symbol": normalized_symbol,
                "type": normalized_type,
                "side": normalized_side,
                "amount": exchange_amount,
                "filled": exchange_amount,
                "remaining": 0.0,
                "contractSize": contract_size,
                "price": price,
                "average": price,
                "status": "closed",
                "reduceOnly": reduce_only,
                "timestamp": fake_now,
                "info": {
                    "dry_run": True,
                    "armed": self.armed,
                },
            }
            normalized = self.normalize_order(fake_order)
            normalized["submitted_at"] = submitted_at
            normalized["acknowledged_at"] = fake_now
            normalized["filled_at"] = fake_now

            return LiveOrderResult(
                ok=True,
                order=normalized,
                status=str(normalized.get("status") or "closed"),
                dry_run=True,
                armed=self.armed,
                submitted_at=submitted_at,
                acknowledged_at=fake_now,
                filled_at=fake_now,
            )

        if self.market_type == "swap" and not reduce_only:
            self.prepare_entry_risk_settings(
                symbol=normalized_symbol,
                leverage=leverage,
                margin_mode=margin_mode,
            )

        created = self.adapter.create_order(
            symbol=normalized_symbol,
            order_type=normalized_type,
            side=normalized_side,
            amount=exchange_amount,
            price=price,
            params=params,
        )

        ack_order = self.normalize_order(created)
        acknowledged_at = ack_order.get("acknowledged_at") or ack_order.get("timestamp") or datetime.now(timezone.utc).isoformat()

        final_order = dict(ack_order)
        final_order["submitted_at"] = submitted_at
        final_order["acknowledged_at"] = acknowledged_at

        order_id = str(final_order.get("id") or "")
        if order_id:
            try:
                refreshed_raw = self.adapter.fetch_order(order_id=order_id, symbol=normalized_symbol)
                refreshed = self.normalize_order(refreshed_raw)
                refreshed["submitted_at"] = submitted_at
                refreshed["acknowledged_at"] = refreshed.get("acknowledged_at") or acknowledged_at
                if refreshed.get("filled_at") is None:
                    status = str(refreshed.get("status") or "").lower()
                    filled_qty = float(refreshed.get("filled") or 0.0)
                    if status in {"closed", "filled"} and filled_qty > 0.0:
                        refreshed["filled_at"] = refreshed.get("acknowledged_at")
                final_order.update(refreshed)
            except Exception as exc:
                final_order["post_submit_verified"] = False
                final_order["post_submit_verification_error"] = f"{type(exc).__name__}: {exc}"
        else:
            final_order["post_submit_verified"] = bool(order_id)

        if final_order.get("filled_at") is None:
            status = str(final_order.get("status") or "").lower()
            filled_qty = float(final_order.get("filled") or 0.0)
            if status in {"closed", "filled"} and filled_qty > 0.0:
                final_order["filled_at"] = final_order.get("acknowledged_at")

        return LiveOrderResult(
            ok=True,
            order=final_order,
            status=str(final_order.get("status") or "open"),
            dry_run=False,
            armed=True,
            submitted_at=submitted_at,
            acknowledged_at=final_order.get("acknowledged_at"),
            filled_at=final_order.get("filled_at"),
        )

    def cancel_order(self, *, symbol: str, order_id: str) -> dict[str, Any]:
        normalized_symbol = self.adapter.normalize_symbol(symbol)
        if not self.can_submit_live_orders:
            return {
                "ok": True,
                "dry_run": True,
                "armed": self.armed,
                "order": {
                    "id": str(order_id),
                    "symbol": normalized_symbol,
                    "status": "canceled",
                },
            }
        cancelled = self.adapter.cancel_order(order_id=str(order_id), symbol=normalized_symbol)
        return {
            "ok": True,
            "dry_run": False,
            "armed": True,
            "order": self.normalize_order(cancelled),
        }

    def fetch_order_status(self, *, symbol: str, order_id: str) -> dict[str, Any]:
        normalized_symbol = self.adapter.normalize_symbol(symbol)
        order = self.adapter.fetch_order(order_id=str(order_id), symbol=normalized_symbol)
        return self.normalize_order(order)

    def fill_from_order(
        self,
        *,
        order: dict[str, Any],
        fill_type: str,
        market_price: float,
        trade_id: int | None,
        expected_price: float | None = None,
        expected_qty: float | None = None,
    ) -> Fill:
        order_status = str(order.get("status") or "unknown").lower()
        is_dry_run_order = bool(order.get("dry_run") or not self.can_submit_live_orders)

        filled_qty = float(order.get("filled_base_qty") or order.get("filled") or 0.0)
        if filled_qty <= 0.0 and is_dry_run_order:
            filled_qty = float(order.get("amount_base_qty") or order.get("amount") or 0.0)

        if not is_dry_run_order and filled_qty <= 0.0 and order_status not in {"closed", "filled"}:
            raise RuntimeError(
                f"Live order is not fill-confirmed yet; refusing to create fill. "
                f"order_id={order.get('id') or ''} status={order_status}"
            )

        price = float(order.get("average") or order.get("price") or market_price)
        fee_cost = self._extract_fee_cost(order)
        side = str(order.get("side") or "buy").lower()
        fill_type_normalized = str(fill_type or "").upper()
        if fill_type_normalized == "ENTRY":
            logical_side = "short" if side == "sell" else "long"
        else:
            logical_side = side
        submitted_at = order.get("submitted_at")
        acknowledged_at = order.get("acknowledged_at")
        filled_at = order.get("filled_at")
        timestamp = str(filled_at or acknowledged_at or order.get("timestamp") or datetime.now(timezone.utc).isoformat())

        effective_expected_price = float(expected_price if expected_price is not None else market_price)
        effective_expected_qty = float(expected_qty if expected_qty is not None else filled_qty)

        price_slippage = price - effective_expected_price
        if side == "sell":
            price_slippage = effective_expected_price - price

        price_slippage_bps = None
        if effective_expected_price > 0.0:
            price_slippage_bps = (price_slippage / effective_expected_price) * 10_000.0

        qty_delta = filled_qty - effective_expected_qty
        qty_delta_pct = None
        if effective_expected_qty > 0.0:
            qty_delta_pct = (qty_delta / effective_expected_qty) * 100.0

        return Fill(
            timestamp=timestamp,
            type=str(fill_type),
            side=logical_side,
            price=price,
            qty=filled_qty,
            fee=fee_cost,
            equity_after=0.0,
            trade_id=trade_id,
            order_id=str(order.get("id") or ""),
            client_order_id=order.get("client_order_id"),
            order_status=order_status,
            exchange=self.exchange_name,
            symbol=str(order.get("symbol") or ""),
            market_type=self.market_type,
            execution_source="live_exchange" if self.can_submit_live_orders else "live_dry_run",
            reduce_only=bool(order.get("reduce_only") or False),
            dry_run=bool(order.get("dry_run") or not self.can_submit_live_orders),
            expected_price=effective_expected_price,
            expected_qty=effective_expected_qty,
            submitted_at=submitted_at,
            acknowledged_at=acknowledged_at,
            filled_at=filled_at,
            submit_to_ack_ms=self._latency_ms(submitted_at, acknowledged_at),
            submit_to_fill_ms=self._latency_ms(submitted_at, filled_at),
            price_slippage=price_slippage,
            price_slippage_bps=price_slippage_bps,
            qty_delta=qty_delta,
            qty_delta_pct=qty_delta_pct,
            order_state=order_status,
            cumulative_qty=filled_qty,
            remaining_qty=float(order.get("remaining_base_qty") or order.get("remaining") or 0.0),
            contract_size=self._float_or_none(order.get("contract_size")),
        )

    def reconcile_order(
        self,
        *,
        symbol: str,
        order_id: str,
        since_ms: int | None = None,
        expected_client_order_id: str | None = None,
    ) -> dict[str, Any]:
        normalized_symbol = self.adapter.normalize_symbol(symbol)
        order = self.fetch_order_status(symbol=normalized_symbol, order_id=order_id)
        trades: list[dict[str, Any]] = []
        try:
            trades = self.fetch_recent_trades(symbol=normalized_symbol, since_ms=since_ms, limit=200)
        except Exception:
            trades = []

        matched: list[dict[str, Any]] = []
        for trade in trades:
            if str(trade.get("order_id") or "") == str(order_id):
                matched.append(trade)
                continue
            if expected_client_order_id and str(trade.get("client_order_id") or "") == str(expected_client_order_id):
                matched.append(trade)

        matched.sort(key=lambda row: str(row.get("timestamp") or ""))
        cumulative_base_qty = sum(float(t.get("base_qty") or 0.0) for t in matched)
        cumulative_fee = sum(float(t.get("fee_cost") or 0.0) for t in matched)
        if cumulative_base_qty > 0.0:
            order["filled_base_qty"] = cumulative_base_qty
            contract_size = float(order.get("contract_size") or 1.0)
            if contract_size > 0 and self.market_type == "swap":
                order["filled"] = cumulative_base_qty / contract_size
            if order.get("amount_base_qty") is not None:
                order["remaining_base_qty"] = max(0.0, float(order.get("amount_base_qty") or 0.0) - cumulative_base_qty)
            if order.get("amount") is not None and self.market_type == "swap" and contract_size > 0:
                order["remaining"] = max(0.0, float(order.get("amount") or 0.0) - float(order.get("filled") or 0.0))
        if cumulative_fee > 0.0:
            order["fee_cost"] = cumulative_fee
            order["fee"] = {"cost": cumulative_fee, "currency": order.get("fee_currency") or self.settle_currency}
        if matched:
            order["fills"] = matched
            order["filled_at"] = matched[-1].get("timestamp") or order.get("filled_at")
        status = str(order.get("status") or "open").lower()
        amount_base = float(order.get("amount_base_qty") or 0.0)
        filled_base = float(order.get("filled_base_qty") or 0.0)
        if status not in {"closed", "canceled"}:
            if amount_base > 0.0 and filled_base > 0.0 and filled_base + 1e-12 < amount_base:
                order["status"] = "partially_filled"
            elif amount_base > 0.0 and filled_base >= amount_base - 1e-12:
                order["status"] = "closed"
        return order

    def normalize_ticker(self, ticker: dict[str, Any] | None) -> dict[str, Any]:
        payload = dict(ticker or {})
        return {
            "symbol": payload.get("symbol"),
            "last": self._float_or_none(payload.get("last")),
            "bid": self._float_or_none(payload.get("bid")),
            "ask": self._float_or_none(payload.get("ask")),
            "timestamp": payload.get("datetime") or payload.get("timestamp"),
            "raw": payload,
        }

    def normalize_balance(self, balance: dict[str, Any] | None) -> dict[str, Any]:
        payload = dict(balance or {})
        return {
            "free": dict(payload.get("free") or {}),
            "used": dict(payload.get("used") or {}),
            "total": dict(payload.get("total") or {}),
            "raw": payload,
        }

    def normalize_order(self, order: dict[str, Any] | None) -> dict[str, Any]:
        payload = dict(order or {})
        fee = payload.get("fee") or {}
        info = payload.get("info") if isinstance(payload.get("info"), dict) else {}
        contract_size = self._resolve_contract_size(
            symbol=payload.get("symbol"),
            payload=payload,
            info=info,
        )
        amount = self._float_or_none(payload.get("amount"))
        filled = self._float_or_none(payload.get("filled"))
        remaining = self._float_or_none(payload.get("remaining"))
        amount_base_qty = amount
        filled_base_qty = filled
        remaining_base_qty = remaining
        if self.market_type == "swap" and contract_size and contract_size > 0:
            if amount is not None:
                amount_base_qty = float(amount) * float(contract_size)
            if filled is not None:
                filled_base_qty = float(filled) * float(contract_size)
            if remaining is not None:
                remaining_base_qty = float(remaining) * float(contract_size)

        acknowledged_at = self._extract_acknowledged_at(payload)
        filled_at = self._extract_filled_at(payload)

        status = str(payload.get("status") or info.get("status") or "open").lower()
        if status == "canceled":
            status = "canceled"
        elif status in {"closed", "filled", "finished"}:
            status = "closed"
        elif status in {"partially_filled", "partial-filled", "open"}:
            status = "open" if not filled or remaining_base_qty else "partially_filled"
        elif (filled_base_qty or 0.0) > 0 and (remaining_base_qty or 0.0) > 0:
            status = "partially_filled"

        return {
            "id": str(payload.get("id") or ""),
            "client_order_id": payload.get("clientOrderId") or payload.get("client_order_id"),
            "symbol": payload.get("symbol"),
            "type": payload.get("type"),
            "side": payload.get("side"),
            "amount": amount,
            "filled": filled,
            "remaining": remaining,
            "amount_base_qty": amount_base_qty,
            "filled_base_qty": filled_base_qty,
            "remaining_base_qty": remaining_base_qty,
            "price": self._float_or_none(payload.get("price")),
            "average": self._float_or_none(payload.get("average")),
            "status": status,
            "reduce_only": bool(payload.get("reduceOnly") or payload.get("reduce_only") or info.get("is_reduce_only") or False),
            "timestamp": self._iso_from_any(payload.get("datetime") or payload.get("timestamp")) or payload.get("datetime") or payload.get("timestamp"),
            "acknowledged_at": acknowledged_at,
            "filled_at": filled_at,
            "fee": fee if isinstance(fee, dict) else {},
            "fee_cost": self._float_or_none(fee.get("cost")) if isinstance(fee, dict) else None,
            "fee_currency": fee.get("currency") if isinstance(fee, dict) else None,
            "contract_size": contract_size,
            "raw": payload,
        }

    def normalize_position(self, position: dict[str, Any] | None) -> dict[str, Any]:
        payload = dict(position or {})
        info = payload.get("info") if isinstance(payload.get("info"), dict) else {}
        contracts = self._float_or_none(payload.get("contracts") or info.get("size"))
        contract_size = self._resolve_contract_size(
            symbol=payload.get("symbol"),
            payload=payload,
            info=info,
        )
        side = payload.get("side")
        if not side and contracts is not None:
            if float(contracts) > 0:
                side = "long"
            elif float(contracts) < 0:
                side = "short"
        base_qty = None
        if contracts is not None:
            base_qty = abs(float(contracts)) * float(contract_size or 1.0)
        return {
            "symbol": payload.get("symbol"),
            "side": side,
            "contracts": abs(float(contracts)) if contracts is not None else None,
            "contract_size": contract_size,
            "base_qty": base_qty,
            "entry_price": self._float_or_none(payload.get("entryPrice") or info.get("entry_price")),
            "mark_price": self._float_or_none(payload.get("markPrice") or info.get("mark_price")),
            "notional": self._float_or_none(payload.get("notional") or payload.get("collateral")),
            "leverage": self._extract_leverage(payload),
            "margin_mode": self._extract_margin_mode(payload),
            "unrealized_pnl": self._float_or_none(payload.get("unrealizedPnl") or info.get("unrealised_pnl")),
            "liquidation_price": self._float_or_none(payload.get("liquidationPrice") or info.get("liq_price")),
            "raw": payload,
        }

    def _extract_fee_cost(self, order: dict[str, Any]) -> float:
        fee = order.get("fee") or {}
        if isinstance(fee, dict):
            fee_cost = self._float_or_none(fee.get("cost"))
            if fee_cost is not None:
                return fee_cost

        fee_cost = self._float_or_none(order.get("fee_cost"))
        if fee_cost is not None:
            return fee_cost

        raw = dict(order.get("raw") or {})
        info = raw.get("info") if isinstance(raw.get("info"), dict) else {}
        cost = self._float_or_none(raw.get("cost") or order.get("cost"))
        fill_price = self._float_or_none(info.get("fill_price") or raw.get("average") or raw.get("price") or order.get("average") or order.get("price"))
        filled_contracts = self._float_or_none(raw.get("filled") or order.get("filled") or raw.get("amount") or order.get("amount"))
        contract_size = self._float_or_none(order.get("contract_size") or raw.get("contractSize") or info.get("quanto_multiplier") or info.get("contract_size"))
        taker_rate = self._float_or_none(info.get("tkfr"))
        maker_rate = self._float_or_none(info.get("mkfr"))
        rate = taker_rate if taker_rate is not None else maker_rate
        if rate is None:
            rate = self._float_or_none(info.get("fee_rate"))
        if rate is not None:
            if cost is None and fill_price is not None and filled_contracts is not None:
                multiplier = float(contract_size or 1.0) if self.market_type == "swap" else 1.0
                cost = abs(float(fill_price) * float(filled_contracts) * multiplier)
            if cost is not None:
                return abs(float(cost) * float(rate))
        return 0.0

    def normalize_trade(self, trade: dict[str, Any] | None) -> dict[str, Any]:
        payload = dict(trade or {})
        fee = payload.get("fee") or {}
        info = payload.get("info") if isinstance(payload.get("info"), dict) else {}
        contract_size = self._resolve_contract_size(
            symbol=payload.get("symbol"),
            payload=payload,
            info=info,
        )
        amount = self._float_or_none(payload.get("amount"))
        base_qty = amount
        if self.market_type == "swap" and amount is not None:
            base_qty = float(amount) * float(contract_size or 1.0)
        fee_cost = self._float_or_none(fee.get("cost")) if isinstance(fee, dict) else None
        if fee_cost is None:
            fee_cost = self._extract_fee_cost({"fee": fee, "raw": payload, "contract_size": contract_size, "filled": amount, "average": payload.get("price")})
        trade_id = payload.get("id") or info.get("id") or info.get("trade_id")
        order_id = payload.get("order") or payload.get("orderId") or info.get("order_id") or info.get("order")
        return {
            "id": str(trade_id or ""),
            "order_id": str(order_id or ""),
            "client_order_id": payload.get("clientOrderId") or info.get("text"),
            "symbol": payload.get("symbol"),
            "side": str(payload.get("side") or "").lower() or None,
            "price": self._float_or_none(payload.get("price")),
            "amount": amount,
            "base_qty": base_qty,
            "cost": self._float_or_none(payload.get("cost")),
            "timestamp": self._iso_from_any(payload.get("datetime") or payload.get("timestamp") or info.get("create_time") or info.get("finish_time")),
            "fee_cost": fee_cost,
            "fee_currency": fee.get("currency") if isinstance(fee, dict) else None,
            "contract_size": contract_size,
            "raw": payload,
        }

    def set_armed(self, armed: bool) -> None:
        self.armed = bool(armed)

    def set_dry_run(self, dry_run: bool) -> None:
        self.dry_run = bool(dry_run)

    def cancel_all_open_orders(self, *, symbol: str) -> dict[str, Any]:
        normalized_symbol = self.adapter.normalize_symbol(symbol)

        if not self.can_submit_live_orders:
            return {
                "ok": True,
                "dry_run": True,
                "armed": self.armed,
                "symbol": normalized_symbol,
                "canceled": [],
            }

        open_orders = self.adapter.fetch_open_orders(normalized_symbol)
        canceled: list[dict[str, Any]] = []

        for raw_order in open_orders:
            order_id = str((raw_order or {}).get("id") or "")
            if not order_id:
                continue
            try:
                result = self.adapter.cancel_order(order_id=order_id, symbol=normalized_symbol)
                canceled.append(self.normalize_order(result))
            except Exception:
                # best effort: continue cancelling the rest
                continue

        return {
            "ok": True,
            "dry_run": False,
            "armed": True,
            "symbol": normalized_symbol,
            "canceled": canceled,
        }
