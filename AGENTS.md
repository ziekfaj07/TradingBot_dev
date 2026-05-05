# 🧠 Trading Bot — Codex Control System

---

## 🔐 CORE ENFORCEMENT RULE

ALL changes MUST go through:

1. Master Orchestrator
2. Relevant Skills
3. Scoring System
4. Final Verdict

If ANY of the above is missing:
→ RESPONSE IS INVALID

---

## ⚙️ MANDATORY EXECUTION FLOW

For EVERY task:

1. Identify task type
2. Activate relevant skills
3. Run Master Orchestrator
4. (If trading logic affected) run Auto-Patch Simulator
5. Output:
   - Report
   - Scores
   - Verdict
   - Patch (ONLY if approved)

---

## 🧠 SKILL AUTO-TRIGGER SYSTEM

### Risk / Capital
- leverage, position, margin → Leverage Safety + Risk Auditor

### Execution
- order, execute, trade, live → Execution Guard + Latency Optimizer

### API
- endpoint, request, response → API Contract Check

### UI
- chart, dashboard, frontend → UI Validator

### Strategy
- strategy, indicator, ATR, signal → Strategy Validator

### Backtesting
- backtest, PnL, metrics → Backtesting Auditor

### Runtime
- live behavior, anomaly, monitoring → Live Monitoring & Anomaly Detection

### Testing
- test, run, deploy → Testing & Commissioning

---

## 🏗️ SYSTEM CONTEXT

### Architecture
- Backend: FastAPI
- Execution: exchange adapters
- Strategy: modular engine
- UI: v0.8 dashboard

### Key Modules
- api.py
- chart.js
- equity.js
- fills.js
- ui-controller.js

---

## 🔁 TRADING MODES

- paper → simulation
- live_dry_run → simulated execution (real data)
- live → real execution (RESTRICTED)

---

## 🚨 CRITICAL SAFETY RULES

- Live trading REQUIRES:
  TRADINGBOT_ALLOW_LIVE_SUBMIT=true

- NEVER allow:
  - implicit leverage
  - missing stop loss
  - unsafe execution path
  - mode confusion

- If detected:
  → IMMEDIATE REJECTION

---

## ⚠️ KNOWN FAILURE POINTS

- Leverage defaulting to 100x
- Mode confusion (live vs demo)
- UI desync issues

Codex MUST actively check these in every relevant task.

---

## 🧾 OUTPUT STANDARD (MANDATORY)

Every response MUST include:

1. Master Orchestrator Report
2. Activated Skills
3. Key Findings
4. Scores:
   - Risk
   - Stability
   - Execution Safety
5. Verdict:
   - APPROVE / CONDITIONAL / REJECT

6. Patch:
   - ONLY if APPROVED or CONDITIONAL

7. Test Instructions:
   - Swagger-based ONLY

---

## 🧪 TESTING STANDARD

- Swagger testing ONLY
- No PowerShell
- Must include exact request configs

---

## 🛑 REJECTION CONDITIONS

Automatically REJECT if:

- Risk Score > 70
- Execution Safety < 50
- Leverage undefined or unsafe
- API contract broken
- Backtest unrealistic
- Monitoring missing in live system

---

## 🔬 SIMULATION REQUIREMENT

If change affects:
- strategy
- execution
- leverage

→ MUST run Auto-Patch Simulator

---

## 🚨 RUNTIME PROTECTION

Live systems MUST include:

- Live Monitoring & Anomaly Detection
- Auto-pause capability

If missing:
→ REJECT deployment

---

## 🎯 CURRENT PRIORITY

- v0.8.2 Chart Integration
  - Candlesticks
  - Entry/exit markers
  - UI stability

---

## 🧠 BEHAVIOR EXPECTATION

- Minimal, surgical patches only
- No large rewrites
- No assumptions about missing code
- Preserve all working features

---

## ⚡ FINAL RULE

This system handles REAL MONEY.

If uncertain:
→ REJECT

Capital preservation > profit