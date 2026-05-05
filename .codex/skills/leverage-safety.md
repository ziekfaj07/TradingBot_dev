# Skill: Leverage Safety

## Role
You enforce strict leverage correctness and prevent unsafe defaults.

## When to Activate
- Any leverage-related logic
- Position sizing
- Order creation

## What You Must Check

### 1. Explicit Leverage
- Must NEVER be:
  - undefined
  - null
  - fallback to exchange default

### 2. Boundaries
- Enforce:
  configured_leverage <= max_leverage
- Reject or clamp invalid values

### 3. Exchange Sync
- Ensure leverage is:
  - set BEFORE order execution
  - confirmed applied

### 4. Hidden Bugs
Detect:
- fallback to 100x
- missing setter calls
- async race conditions

## Output
- Leverage Risk: LOW / HIGH / CRITICAL
- Root Cause
- Patch

## Hard Rules
- No implicit leverage
- No execution without confirmed leverage