"""Deterministic policies that turn analytics into proposed actions.

This is the layer the project's second principle — "code calculates and decides" — was missing.
Code already calculated diagnostics (savings rate, HHI, the need vector) and decided routing
(which advisors to convene), but the decision the user actually came for was invented by the
model in prose. These modules compute it instead.

Three properties every policy here holds to:

  Pure. `(profile, analytics, portfolio, portfolio_analytics, guardrails, params) -> actions`.
  No I/O, no model, no clock. That is what makes them testable against known answers, and it is
  also what lets them run on the free tier: a policy costs nothing and needs no API key.

  Parameterized by the persona, not by the author. Every threshold comes from
  `PolicyParameters` on the advisor manifest. Two advisors running the same policy over the same
  portfolio produce different actions, and the difference is legible as a number.

  Honest about sequence. Resolving a blocking guardrail outranks anything optional, so proceeds
  from a trim pay down 22.9% debt before they buy anything.

What they deliberately do not do is decide whether their own advice is wise. A policy proposes;
`analytics/counterfactual.py` scores; the committee argues; the user chooses.
"""
