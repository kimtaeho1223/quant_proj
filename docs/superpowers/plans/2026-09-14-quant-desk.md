# Quant Desk KR Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** A local Korean-language Streamlit demo for weekly multifactor portfolio decisions.

**Architecture:** Pure pandas scoring and rebalancing functions, deterministic fictional sample data, SQLite persistence, and a Streamlit interface. All quotes and metrics are fictional; live collection is the next milestone.

**Tech Stack:** Python, pandas, Streamlit, SQLite, unittest.

**Spec:** The accepted requirements are recorded below.

## Global Constraints

- Momentum/value/quality/low-volatility weights: 45/20/20/15; editable with total 100.
- Target 20 equal-weight names. Keep eligible existing names ranked <=30; fill remaining slots from top 20. If more than 20 existing names qualify, retain highest ranked 20.
- Exclude failed eligibility, missing factor inputs, nonpositive prices, low liquidity and small capitalization.
- Buy whole shares only; ignore trades under KRW 100,000, including exits. Show residual positions explicitly.
- Account for a configurable transaction-cost reserve. Sell proceeds fund buys; cash cannot go negative.
- Persist holdings and decision snapshots locally. Saving a decision does not execute orders or change holdings.
- No claimed investment returns, live market data, or automated brokerage orders.

## Tasks

Completed 2026-09-14: all four tasks below were implemented. Eight unittest/AppTest checks pass; localhost health returns `ok`; the rendered dashboard was visually checked in the in-app browser. Live collection remains a separate next milestone.

- [ ] Core: create `tests/test_core.py`, run `python -m unittest discover -s tests -v` to observe missing modules, then implement `quantdesk/core.py` with `score_stocks(data, weights)` and `rebalance(ranked, holdings, cash, min_trade, fee_rate)`. Test rank retention, failed filters, budget, integer quantities, and minimum trade.
- [ ] Data and persistence: implement `quantdesk/data.py` with deterministic samples and `quantdesk/storage.py` with SQLite holdings/settings/snapshots. Test round-trip using temporary databases.
- [ ] UI: implement `app.py` with dashboard, rankings, editable holdings, order proposals and journal. Use Streamlit AppTest for navigation, settings, validation and persistence workflows.
- [ ] Delivery: document launch and scope in `README.md`, add `requirements.txt` and `start.ps1`, run tests, launch localhost, verify HTTP health and browser layout where available.
