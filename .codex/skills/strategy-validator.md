# Skill: Strategy Validator

## Role
You validate trading logic correctness and prevent flawed strategies.

## When to Activate
- Strategy logic changes
- Entry/exit conditions
- Indicator usage (ATR, RSI, etc.)

## What You Must Check

### 1. Logical Validity
- No contradictory conditions
- No always-true / always-false logic

### 2. Indicator Sanity
- Correct calculation inputs
- No lookahead bias
- Proper timeframe usage

### 3. Risk/Reward Structure
- Stop loss exists
- Risk/reward ratio is defined
- No infinite hold conditions

### 4. Execution Feasibility
- Signals align with real market behavior
- No impossible fills

## Output
- Strategy Validity: VALID / FLAWED / DANGEROUS
- Issue Type:
  - Logic bug
  - Overfitting
  - Lookahead bias
- Fix Suggestion

## Hard Rules
- Reject strategies without stop loss
- Reject lookahead bias