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

    @property
    def can_submit_live_orders(self) -> bool:
        return self.armed and not self.dry_run

    def sync_account(self, *, symbol: str) -> dict[str, Any]:
        normalized_symbol = self.adapter.normalize_symbol(symbol)
        ticker = self.adapter.fetch_ticker(normalized_symbol)
        balance = self.adapter.fetch_balance()
        open_orders = self.adapter.fetch_open_orders(normalized_symbol)
        positions: list[dict[str, Any]] = []
        if self.market_type == "swap":
            positions = self.adapter.fetch_positions(normalized_symbol)
        return {
            "exchange": self.exchange_name,
            "market_type": self.market_type,
            "symbol": normalized_symbol,
            "environment": self.adapter.describe().get("environment"),
            "settle_currency": self.settle_currency,
            "credential_profile": self.credential_profile,
            "resolved_api_key_env": self.resolved_api_key_env,
            "resolved_api_secret_env": self.resolved_api_secret_env,
            "ticker": self.normalize_ticker(ticker),
            "balance": self.normalize_balance(balance),
            "open_orders": [self.normalize_order(x) for x in open_orders],
            "positions": [self.normalize_position(x) for x in positions],
            "synced_at": datetime.now(timezone.utc).isoformat(),
            "armed": self.armed,
            "dry_run": self.dry_run,
        }

    @staticmethod
    def _float_or_none(value: Any) -> float | None:
        try:
            if value is None or value == "":
                return None
            return float(value)
        except (TypeError, ValueError):
            return None

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
    ) -> LiveOrderResult:
        normalized_symbol = self.adapter.normalize_symbol(symbol)
        normalized_side = str(side or "buy").strip().lower()
        normalized_type = str(order_type or "market").strip().lower()
        requested_qty = float(qty)
        if requested_qty <= 0.0:
            raise ExchangeConfigurationError("Order qty must be > 0.")

        submitted_at = datetime.now(timezone.utc).isoformat()

        params: dict[str, Any] = {}
        if client_order_id:
            params["text"] = str(client_order_id)
        if reduce_only and self.market_type == "swap":
            params["reduce_only"] = True

        if not self.can_submit_live_orders:
            fake_now = datetime.now(timezone.utc).isoformat()
            fake_order = {
                "id": f"dryrun-{int(datetime.now(timezone.utc).timestamp() * 1000)}",
                "clientOrderId": client_order_id,
                "symbol": normalized_symbol,
                "type": normalized_type,
                "side": normalized_side,
                "amount": requested_qty,
                "filled": requested_qty,
                "remaining": 0.0,
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

        created = self.adapter.create_order(
            symbol=normalized_symbol,
            order_type=normalized_type,
            side=normalized_side,
            amount=requested_qty,
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
            except Exception:
                pass

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
        filled_qty = float(order.get("filled") or order.get("amount") or 0.0)
        price = float(order.get("average") or order.get("price") or market_price)
        fee_cost = self._extract_fee_cost(order)
        side = str(order.get("side") or "buy").lower()
        logical_side = "long" if side == "buy" else "sell"
        order_status = str(order.get("status") or "unknown").lower()

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
        )

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

        acknowledged_at = self._extract_acknowledged_at(payload)
        filled_at = self._extract_filled_at(payload)

        return {
            "id": str(payload.get("id") or ""),
            "client_order_id": payload.get("clientOrderId") or payload.get("client_order_id"),
            "symbol": payload.get("symbol"),
            "type": payload.get("type"),
            "side": payload.get("side"),
            "amount": self._float_or_none(payload.get("amount")),
            "filled": self._float_or_none(payload.get("filled")),
            "remaining": self._float_or_none(payload.get("remaining")),
            "price": self._float_or_none(payload.get("price")),
            "average": self._float_or_none(payload.get("average")),
            "status": payload.get("status"),
            "reduce_only": bool(payload.get("reduceOnly") or payload.get("reduce_only") or False),
            "timestamp": self._iso_from_any(payload.get("datetime") or payload.get("timestamp")) or payload.get("datetime") or payload.get("timestamp"),
            "acknowledged_at": acknowledged_at,
            "filled_at": filled_at,
            "fee_cost": self._float_or_none(fee.get("cost")) if isinstance(fee, dict) else None,
            "fee_currency": fee.get("currency") if isinstance(fee, dict) else None,
            "raw": payload,
        }

    def normalize_position(self, position: dict[str, Any] | None) -> dict[str, Any]:
        payload = dict(position or {})
        return {
            "symbol": payload.get("symbol"),
            "side": payload.get("side"),
            "contracts": self._float_or_none(payload.get("contracts")),
            "contract_size": self._float_or_none(payload.get("contractSize")),
            "entry_price": self._float_or_none(payload.get("entryPrice")),
            "mark_price": self._float_or_none(payload.get("markPrice")),
            "notional": self._float_or_none(payload.get("notional")),
            "leverage": self._float_or_none(payload.get("leverage")),
            "margin_mode": payload.get("marginMode"),
            "unrealized_pnl": self._float_or_none(payload.get("unrealizedPnl")),
            "liquidation_price": self._float_or_none(payload.get("liquidationPrice")),
            "raw": payload,
        }

    def _extract_fee_cost(self, order: dict[str, Any]) -> float:
        fee = order.get("fee") or {}
        if isinstance(fee, dict):
            fee_cost = self._float_or_none(fee.get("cost"))
            if fee_cost is not None:
                return fee_cost
        return 0.0

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

    @staticmethod
    def _float_or_none(value: Any) -> float | None:
        try:
            if value is None:
                return None
            return float(value)
        except (TypeError, ValueError):
            return None