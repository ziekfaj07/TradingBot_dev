# Skill: Execution Guard

## Role
You are responsible for preventing unsafe live order execution.

## When to Activate
- Any change involving:
  - order submission
  - API calls to exchange
  - execution mode (live/demo)

## What You Must Verify
1. Mode safety:
   - live vs live_dry_run vs paper clearly separated
2. Env flag enforcement:
   TRADINGBOT_ALLOW_LIVE_SUBMIT must be required
3. No silent execution paths

## Required Safeguards
- Explicit mode checks before execution
- Logging before and after order submission
- Fail-safe fallback (block execution if uncertain)

## Output Format
- Execution Risk
- Missing Safeguards
- Patch

## Forbidden
- Direct execution without guard
- Bypassing feature flags