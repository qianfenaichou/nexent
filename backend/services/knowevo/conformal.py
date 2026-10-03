"""Split-conformal acceptance line for proposal auto-accept (2026-09-28).

The fixed ``KW_AUTO_ACCEPT_LINE`` (0.85) is a picked number: nothing ties it
to how often the gate is wrong. This module replaces the picked line with a
calibrated one when calibration data exists - the confidence scores of
proposals a human REJECTED (the "bad" class) - using one-sided split
conformal prediction, stdlib-only (no new dependency, contract preserved).

Guarantee, stated honestly: for a fresh bad proposal whose score is
exchangeable with the calibration scores,

    P(score of new bad proposal > line) <= alpha

i.e. at most ``alpha`` of rejected-class proposals are expected to clear
the line (per-bad-proposal false-accept rate <= alpha; the share of bad
proposals among all auto-accepted also depends on the base rate). Ties
only make this more conservative, because acceptance is strict ``>``.
This is a marginal, distribution-free statement - it does not need the
score distribution, only exchangeability, and it degrades honestly: when
the calibration class is too small for a finite guarantee
(``ceil((n+1)*(1-alpha)) > n``, i.e. n < 19 at alpha=0.05), the function
returns None and the caller must fall back to the fixed line instead of
pretending precision the data cannot support.

Method source: Vovk et al., Algorithmic Learning in a Random World
(split/conformal prediction); applied to human-review gating.
"""
from __future__ import annotations

import math
from collections.abc import Iterable

DEFAULT_ALPHA = 0.05


def conformal_accept_line(bad_scores: Iterable[float],
                          alpha: float = DEFAULT_ALPHA) -> float | None:
    """One-sided split-conformal threshold over the "bad" calibration class.

    ``bad_scores`` are confidence values of human-rejected proposals (higher
    = the gate would have wrongly accepted them). Returns the score line
    ``tau`` such that a new exchangeable bad score exceeds ``tau`` with
    probability <= ``alpha``: accept a fresh proposal only if its score is
    STRICTLY above ``tau``.

    Returns None when no finite line can carry the guarantee
    (``ceil((n+1)*(1-alpha)) > n``) - e.g. any n < 19 at alpha=0.05 - or the
    class is empty. Callers must fall back to the fixed line in that case;
    returning a number here would be fabricating a guarantee.
    """
    if not 0.0 < alpha < 1.0:
        raise ValueError(f"alpha must be in (0, 1), got {alpha!r}")
    scores = sorted(float(s) for s in bad_scores)
    n = len(scores)
    if n == 0:
        return None
    # Rank of a fresh bad point among n+1 exchangeable values: accepting
    # only above the k-th smallest calibration score keeps the exceedance
    # probability at (n+1-k)/(n+1) <= alpha.
    k = math.ceil((n + 1) * (1.0 - alpha))
    if k > n:
        return None
    return scores[k - 1]
