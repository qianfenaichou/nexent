"""Unit tests for services/knowevo/conformal.py (L3) and the
auto-accept line resolver that wires it into ontology_service.

Layer 1 (always runs, fully offline): the conformal quantile math against
hand-computed order statistics, a seeded Monte-Carlo coverage check of the
finite-sample guarantee, and the resolver's fallback wiring with a faked
database session (no Postgres, no LLM).
"""
import math
import random
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[4]
for _p in (str(_REPO_ROOT / "backend"), ):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest

from services.knowevo.conformal import conformal_accept_line
from services.knowevo.ontology_service import AUTO_ACCEPT_LINE, OntologyService

TENANT = "11111111-1111-1111-1111-111111111111"


class TestConformalMath:
    def test_n20_line_is_the_maximum(self):
        # ceil((20+1)*0.95) = ceil(19.95) = 20 -> the largest calibration
        # score: P(fresh bad > max of 20) = 1/21 <= 0.05.
        scores = [0.10 * (i + 1) for i in range(20)]  # 0.1 .. 2.0
        assert conformal_accept_line(scores) == pytest.approx(2.0)

    def test_n39_line_is_second_largest(self):
        scores = sorted((i % 17) * 0.05 + 0.05 for i in range(39))
        line = conformal_accept_line(scores)
        assert line == pytest.approx(sorted(scores)[-2])

    def test_small_class_returns_none(self):
        # n=18: ceil(19*0.95) = 19 > 18 -> no finite line carries the
        # guarantee; the caller must fall back instead of pretending.
        assert conformal_accept_line([0.5] * 18) is None

    def test_n19_boundary_still_works(self):
        # n=19: ceil(20*0.95) = 19 -> line = max; P = 1/20 = 0.05.
        assert conformal_accept_line([0.5] * 19) == pytest.approx(0.5)

    def test_empty_returns_none(self):
        assert conformal_accept_line([]) is None

    def test_alpha_out_of_range_raises(self):
        with pytest.raises(ValueError):
            conformal_accept_line([0.5], alpha=0.0)
        with pytest.raises(ValueError):
            conformal_accept_line([0.5], alpha=1.0)

    def test_floats_are_coerced(self):
        assert conformal_accept_line([1, 2, "0.5"] * 7) is not None


class TestCoverageGuarantee:
    def test_seeded_monte_carlo_exceedance_at_most_alpha(self):
        # Fresh bad scores exchangeable with the calibration class: the
        # empirical exceedance rate must respect the finite-sample bound
        # (tolerance for Monte-Carlo noise, seed fixed for reproducibility).
        rng = random.Random(7)
        trials = 2000
        n = 40
        exceed = 0
        for _ in range(trials):
            cal = [min(1.0, max(0.0, rng.gauss(0.5, 0.10))) for _ in range(n)]
            fresh = min(1.0, max(0.0, rng.gauss(0.5, 0.10)))
            line = conformal_accept_line(cal)
            if line is not None and fresh > line:
                exceed += 1
        rate = exceed / trials
        assert rate <= 0.05 + 0.02, f"exceedance rate {rate:.4f} > alpha+tol"

    def test_good_scores_clear_the_line(self):
        # A well-separated good class should mostly clear the line. This is
        # a separation sanity check, NOT part of the guarantee: conformal
        # bounds the bad-class exceedance at alpha; how many goods pass
        # depends on the score distributions (here ~3.4 sigma separation).
        rng = random.Random(11)
        cal = [min(1.0, max(0.0, rng.gauss(0.4, 0.08))) for _ in range(40)]
        line = conformal_accept_line(cal)
        good = [min(1.0, max(0.0, rng.gauss(0.8, 0.08))) for _ in range(500)]
        precision = sum(1 for g in good if g > line) / len(good)
        assert precision >= 0.95


class _FakeSession:
    """Minimal query chain: query(col).filter(...).all() -> canned rows.

    Filter conditions are recorded so a test can pin the WHERE clause
    (tenant + status) instead of silently passing whatever was asked.
    """

    def __init__(self, rows):
        self._rows = rows
        self.filters: list = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def query(self, _model_col):
        return self

    def filter(self, *args):
        self.filters.extend(args)
        return self

    def all(self):
        return self._rows


class TestResolveAutoAcceptLine:
    @pytest.mark.asyncio
    async def test_falls_back_when_no_calibration(self, monkeypatch):
        import database.knowevo_db as db
        monkeypatch.setattr(db, "_get_db_session",
                            lambda: _FakeSession([]))
        svc = OntologyService()
        line, meta = await svc.resolve_auto_accept_line(TENANT)
        assert line == AUTO_ACCEPT_LINE
        assert meta["method"] == "fixed_fallback"
        assert meta["n_calibration"] == 0

    @pytest.mark.asyncio
    async def test_conformal_line_from_rejected_confidences(self, monkeypatch):
        import database.knowevo_db as db
        rng = random.Random(3)
        rows = [(min(1.0, max(0.0, rng.gauss(0.5, 0.10))),) for _ in range(40)]
        monkeypatch.setattr(db, "_get_db_session",
                            lambda: _FakeSession(rows))
        svc = OntologyService()
        line, meta = await svc.resolve_auto_accept_line(TENANT)
        expected = conformal_accept_line([r[0] for r in rows])
        assert meta["method"] == "conformal"
        assert meta["n_calibration"] == 40
        assert line == pytest.approx(expected)
        assert line <= max(r[0] for r in rows)

    @pytest.mark.asyncio
    async def test_none_confidences_are_skipped(self, monkeypatch):
        import database.knowevo_db as db
        rows = [(0.4,), (None,), (0.5,)] * 20  # 60 rows, 40 usable
        monkeypatch.setattr(db, "_get_db_session",
                            lambda: _FakeSession(rows))
        svc = OntologyService()
        line, meta = await svc.resolve_auto_accept_line(TENANT)
        assert meta["method"] == "conformal"
        assert meta["n_calibration"] == 40
        assert math.isfinite(line)
