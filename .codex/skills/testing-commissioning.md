# Skill: Testing & Commissioning

## Role
You provide a complete, step-by-step manual testing and deployment validation procedure.

---

## When to Activate
- After feature completion
- Before live deployment
- When user requests “ready for production?”

---

## Output Structure

You MUST provide:

---

# 🧪 MANUAL TESTING PROCEDURE

## Phase 1 — Environment Setup

1. Verify `.env`:
   - TRADINGBOT_ALLOW_LIVE_SUBMIT=false
   - Correct API keys (demo first)

2. Start backend:
   - run FastAPI server

3. Open Swagger UI:
   - confirm all endpoints load

---

## Phase 2 — API Testing (Swagger ONLY)

Test each endpoint:

### /order/submit
- Send test order
- Expect:
  - correct response schema
  - no execution in dry mode

### /position
- Verify positions reflect correctly

### /balance
- Confirm values match exchange

---

## Phase 3 — Execution Mode Testing

### Test Modes:

1. paper
2. live_dry_run
3. live (ONLY if safe)

Verify:
- mode switching works
- no cross-mode contamination

---

## Phase 4 — Strategy Validation

- Trigger entry conditions manually
- Confirm:
  - correct signal generation
  - stop loss applied
  - take profit applied

---

## Phase 5 — UI Validation (v0.8)

- Open dashboard
- Verify:
  - candlestick chart loads
  - entry/exit markers correct
  - equity updates in real-time

---

## Phase 6 — Failure Testing

Simulate:

- API failure
- network delay
- partial fills

Expected:
- system does NOT crash
- retries or safe fallback

---

## Phase 7 — Dry Run Simulation

Run bot in:
- live_dry_run mode

Observe:
- trades simulated correctly
- no real execution

---

## Phase 8 — Limited Live Test (FINAL)

ONLY if all above pass:

1. Enable:
   TRADINGBOT_ALLOW_LIVE_SUBMIT=true

2. Use:
   - minimal capital
   - lowest leverage

3. Execute:
   - 1–2 trades only

4. Verify:
   - correct execution
   - correct PnL tracking

---

# ✅ COMMISSIONING CHECKLIST

## System Integrity
- [ ] No API errors
- [ ] No console errors
- [ ] Logs clean

## Trading Safety
- [ ] Leverage correct
- [ ] Position sizing correct
- [ ] Stop loss always present

## Execution Safety
- [ ] Live mode gated
- [ ] No accidental execution
- [ ] Orders match expectations

## UI Accuracy
- [ ] Chart matches backend
- [ ] Trades displayed correctly
- [ ] No lag/desync

## Backtesting Validity
- [ ] Includes fees
- [ ] Includes slippage
- [ ] No unrealistic results

## Performance
- [ ] No major latency spikes
- [ ] No blocking calls

---

## Final Decision

- ✅ READY FOR DEPLOYMENT
- ⚠️ NEEDS FIXES
- ❌ NOT SAFE

---

## Hard Rules

- Never approve deployment without dry run
- Never skip failure testing
- Never allow live trading without safeguards