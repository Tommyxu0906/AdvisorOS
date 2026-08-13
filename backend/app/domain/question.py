"""User question and its deterministic intent classification.

Intent extraction is keyword-driven so that `/api/committee/select` needs no API key.
An optional LLM refinement exists but is never required.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class QuestionTopic(str, Enum):
    allocation = "allocation"
    concentration = "concentration"
    debt = "debt"
    retirement = "retirement"
    tax = "tax"
    market_timing = "market_timing"
    valuation = "valuation"
    emergency_fund = "emergency_fund"
    behavior = "behavior"
    general = "general"


# Deterministic keyword map. Order does not matter; all matches are collected.
_TOPIC_KEYWORDS: dict[QuestionTopic, tuple[str, ...]] = {
    QuestionTopic.allocation: (
        "allocation",
        "allocate",
        "rebalance",
        "asset mix",
        "diversify",
        "diversification",
        "bond",
        "equity",
        "stock/bond",
        "portfolio mix",
        "index fund",
        "etf",
    ),
    QuestionTopic.concentration: (
        "concentrated",
        "concentration",
        "single stock",
        "one stock",
        "rsu",
        "espp",
        "company stock",
        "too much of",
        "position size",
    ),
    QuestionTopic.debt: (
        "debt",
        "loan",
        "mortgage",
        "credit card",
        "student loan",
        "pay off",
        "payoff",
        "interest rate",
        "apr",
        "refinance",
    ),
    QuestionTopic.retirement: (
        "retire",
        "retirement",
        "401k",
        "401(k)",
        "ira",
        "pension",
        "withdrawal rate",
        "social security",
        "nest egg",
    ),
    QuestionTopic.tax: (
        "tax",
        "taxes",
        "taxable",
        "roth",
        "conversion",
        "capital gain",
        "tax loss",
        "harvest",
        "deduction",
        "hsa",
    ),
    QuestionTopic.market_timing: (
        "timing",
        "time the market",
        "crash",
        "correction",
        "recession",
        "wait for",
        "buy the dip",
        "lump sum",
        "dollar cost",
        "right now",
        "bubble",
    ),
    QuestionTopic.valuation: (
        "valuation",
        "overvalued",
        "undervalued",
        "expensive",
        "cheap",
        "p/e",
        "pe ratio",
        "cape",
        "intrinsic value",
        "margin of safety",
        "fair value",
    ),
    QuestionTopic.emergency_fund: (
        "emergency fund",
        "emergency savings",
        "rainy day",
        "cash cushion",
        "safety net",
        "job loss",
        "laid off",
    ),
    QuestionTopic.behavior: (
        "panic",
        "anxious",
        "worried",
        "scared",
        "fomo",
        "regret",
        "should i sell",
        "can't sleep",
        "stressed",
        "nervous",
    ),
}

_URGENCY_MARKERS = ("right now", "today", "urgent", "immediately", "asap", "this week", "tomorrow")
_DECISION_MARKERS = (
    "should i",
    "do i",
    "is it worth",
    "would you",
    "what would you do",
    "help me decide",
)


class QuestionIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    topics: list[QuestionTopic] = Field(default_factory=list)
    is_decision_request: bool = False
    has_urgency: bool = False
    matched_terms: list[str] = Field(default_factory=list)

    @property
    def primary_topic(self) -> QuestionTopic:
        return self.topics[0] if self.topics else QuestionTopic.general


class UserQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=4000)

    def classify(self) -> QuestionIntent:
        """Deterministic, allocation-free-of-Claude intent extraction."""
        lowered = self.text.lower()
        scored: list[tuple[int, QuestionTopic]] = []
        matched: list[str] = []

        for topic, keywords in _TOPIC_KEYWORDS.items():
            hits = [k for k in keywords if k in lowered]
            if hits:
                scored.append((len(hits), topic))
                matched.extend(hits)

        # Most keyword hits first; ties broken by declaration order for stability.
        topic_order = list(_TOPIC_KEYWORDS)
        scored.sort(key=lambda st: (-st[0], topic_order.index(st[1])))
        topics = [t for _, t in scored] or [QuestionTopic.general]

        return QuestionIntent(
            topics=topics,
            is_decision_request=any(m in lowered for m in _DECISION_MARKERS),
            has_urgency=any(m in lowered for m in _URGENCY_MARKERS),
            matched_terms=sorted(set(matched)),
        )
