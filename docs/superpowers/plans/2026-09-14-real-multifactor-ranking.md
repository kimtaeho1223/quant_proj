# Real Multifactor Ranking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rank the top 100 eligible saved KRX listings using four real factors and show data gaps.

**Architecture:** `quantdesk/real_ranking.py` builds the candidate universe, selects filing-date-eligible financial statements, derives price metrics, and delegates percentile scores to the existing core scorer. `quantdesk/real_ranking_ui.py` reads local market/DART stores and presents readiness, ranks, and exclusions.

**Tech Stack:** Python, pandas, Streamlit, SQLite, unittest.

**Spec:** `docs/superpowers/specs/2026-09-14-real-multifactor-ranking-design.md`

## Global Constraints

- Candidate universe is exactly the top 100 valid KOSPI/KOSDAQ common-share candidates by saved market-cap snapshot.
- A rank requires 252 local closes and a verified annual statement filed on or before the selected date.
- Ranking is local-data-only and never sends orders.
- Current market capitalization is displayed as a snapshot-based value reference, not a backtest input.

### Task 1: Ranking Domain

**Files:**
- Create: `tests/test_real_ranking.py`
- Create: `quantdesk/real_ranking.py`

- [x] Write failing tests for common-share filtering, 252-day momentum/volatility, filing-date selection, CFS preference, and excluded reasons.
- [x] Run `python -m unittest discover -s tests -p test_real_ranking.py -v` and verify import failure.
- [x] Implement candidate, statement selection, metric and ranking functions.
- [x] Run the focused tests and verify passing output.

### Task 2: Ranking Screen

**Files:**
- Create: `quantdesk/real_ranking_ui.py`
- Modify: `app.py`
- Modify: `tests/test_app.py`

- [x] Add a failing AppTest for the actual-ranking navigation and empty-data state.
- [x] Implement the local-data ranking screen with selected date, readiness summary, ranked table and exclusions.
- [x] Run the full test suite and verify passing output.

### Task 3: Documentation and Delivery

**Files:**
- Modify: `README.md`
- Modify: `CHANGELOG.md`

- [x] Document definitions, data sufficiency requirements and current-view limitation.
- [x] Run `python -m unittest discover -s tests -v` and `git diff --check`.
- [x] Commit and push the verified feature.
