"""Investor policy distillation from revealed decisions.

Pipeline, of which this branch currently implements the first two stages:

    holdings snapshots ─→ drift-adjusted action extraction ─→ DecisionEpisode
                                                                   │
                              (later) replay ─→ score ─→ patch ────┘

`drift.py` answers "what did they actually do", which is harder than it looks: a position going
from 10% to 13% of a portfolio may be a purchase or may be a stock that went up. `episode.py`
answers "what could they see when they did it", and enforces that answer with types rather than
with care.
"""
