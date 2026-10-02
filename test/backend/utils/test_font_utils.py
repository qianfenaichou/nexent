"""UT for backend/utils/font_utils.py — CJK font discovery & registration."""

import importlib.util as _ilu
import sys
_STUB_MODULE_SNAPSHOT = dict(sys.modules)
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

_BACKEND = Path(__file__).resolve().parents[3] / "backend"


def _load_module(name, rel_path):
    if name in sys.modules:
        del sys.modules[name]
    spec = _ilu.spec_from_file_location(name, str(_BACKEND / rel_path))
    assert spec is not None and spec.loader is not None
    mod = _ilu.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


font_utils = _load_module("font_utils", "utils/font_utils.py")


@pytest.fixture(autouse=True)
def _reset_cache():
    font_utils._CACHED_FONT_PATH = None
    font_utils._CACHED_FONT_NAME = None
    yield
    font_utils._CACHED_FONT_PATH = None
    font_utils._CACHED_FONT_NAME = None


def _install_matplotlib_stub(addfont_side_effect=None, font_name="TestCJK", ttflist=None):
    fm = MagicMock()
    fm.FontProperties.return_value.get_name.return_value = font_name
    fm.fontManager.addfont.side_effect = addfont_side_effect
    fm.fontManager.ttflist = ttflist or []
    mpl = MagicMock()
    mpl.font_manager = fm
    sys.modules["matplotlib"] = mpl
    sys.modules["matplotlib.font_manager"] = fm
    return fm


def _install_reportlab_stub(register_side_effect=None):
    pdfmetrics = MagicMock()
    pdfmetrics.registerFont.side_effect = register_side_effect
    ttfonts = MagicMock()
    cidfonts = MagicMock()
    rl = MagicMock()
    rl.pdfbase.pdfmetrics = pdfmetrics
    rl.pdfbase.ttfonts = ttfonts
    rl.pdfbase.cidfonts = cidfonts
    sys.modules["reportlab"] = rl
    sys.modules["reportlab.pdfbase"] = rl.pdfbase
    sys.modules["reportlab.pdfbase.pdfmetrics"] = pdfmetrics
    sys.modules["reportlab.pdfbase.ttfonts"] = ttfonts
    sys.modules["reportlab.pdfbase.cidfonts"] = cidfonts
    return pdfmetrics, ttfonts, cidfonts


class TestFindCjkFontPath:
    def test_prefers_linux_fontconfig_result(self, monkeypatch):
        monkeypatch.setattr(font_utils.shutil, "which", lambda _name: "/usr/bin/fc-match")
        monkeypatch.setattr(
            font_utils.subprocess,
            "run",
            lambda *_args, **_kwargs: SimpleNamespace(
                stdout="Noto Sans CJK SC\tzh-cn|zh-tw\t/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc\n"
            ),
        )
        monkeypatch.setattr(font_utils.os.path, "exists", lambda _path: True)
        monkeypatch.setattr(font_utils.os.path, "getsize", lambda _path: 2001)

        assert font_utils._find_cjk_font_path() == (
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
        )

    def test_fontconfig_failure_falls_back_to_candidates(self, monkeypatch):
        monkeypatch.setattr(font_utils.shutil, "which", lambda _name: "/usr/bin/fc-match")

        def _raise(*_args, **_kwargs):
            raise OSError("fc-match unavailable")

        monkeypatch.setattr(font_utils.subprocess, "run", _raise)
        monkeypatch.setattr(font_utils.os.path, "exists", lambda _path: False)

        assert font_utils._find_cjk_font_path() is None

    def test_ignores_invalid_or_non_cjk_fontconfig_results(self, monkeypatch):
        monkeypatch.setattr(font_utils.shutil, "which", lambda _name: "/usr/bin/fc-match")
        monkeypatch.setattr(
            font_utils.subprocess,
            "run",
            lambda *_args, **_kwargs: SimpleNamespace(
                stdout="malformed\nNoto Sans CJK SC\t\nDejaVu Sans\ten\t/foo.ttf\n"
            ),
        )
        monkeypatch.setattr(font_utils.os.path, "exists", lambda _path: False)

        assert font_utils._find_cjk_font_path() is None

    def test_uses_explicit_linux_path_when_fontconfig_has_no_result(self, monkeypatch):
        monkeypatch.setattr(font_utils, "_find_cjk_font_with_fontconfig", lambda: None)
        expected = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
        monkeypatch.setattr(font_utils.os.path, "exists", lambda path: path == expected)
        monkeypatch.setattr(font_utils.os.path, "getsize", lambda _path: 2001)

        assert font_utils._find_cjk_font_path() == expected

    def test_returns_real_font_when_present(self):
        fp = font_utils._find_cjk_font_path()
        assert fp is None or (Path(fp).exists() and Path(fp).stat().st_size > 1000)

    @patch("os.path.exists", return_value=False)
    def test_returns_none_when_no_candidate_exists(self, _exists):
        assert font_utils._find_cjk_font_path() is None

    @patch("os.path.getsize", return_value=100)
    @patch("os.path.exists", return_value=True)
    def test_skips_too_small_file(self, _exists, _getsize):
        assert font_utils._find_cjk_font_path() is None


class TestGetCjkFontPath:
    @patch("font_utils._find_cjk_font_path", return_value="C:/x.ttf")
    def test_caches_hit(self, finder):
        assert font_utils.get_cjk_font_path() == "C:/x.ttf"
        assert font_utils.get_cjk_font_path() == "C:/x.ttf"
        finder.assert_called_once_with()

    @patch("font_utils._find_cjk_font_path", return_value=None)
    def test_caches_miss(self, _finder):
        assert font_utils.get_cjk_font_path() is None
        assert font_utils.get_cjk_font_path() is None


class TestSetupMatplotlibCjk:
    def test_uses_cache(self, monkeypatch):
        monkeypatch.setattr(font_utils, "_CACHED_FONT_NAME", "cached")
        assert font_utils.setup_matplotlib_cjk() == "cached"

    @patch("font_utils.get_cjk_font_path", return_value="C:/x.ttf")
    def test_registers_font_and_returns_family(self, _fp):
        fm = _install_matplotlib_stub()
        assert font_utils.setup_matplotlib_cjk() == "TestCJK"
        fm.fontManager.addfont.assert_called_once_with("C:/x.ttf")

    @patch("font_utils.get_cjk_font_path", return_value="C:/x.ttf")
    def test_register_error_falls_back_to_scan(self, _fp):
        fm = _install_matplotlib_stub(
            addfont_side_effect=RuntimeError("no"), ttflist=[SimpleNamespace(name="Noto Sans CJK SC")]
        )
        assert font_utils.setup_matplotlib_cjk() == "Noto Sans CJK SC"

    @patch("font_utils.get_cjk_font_path", return_value="C:/x.ttf")
    def test_no_match_returns_sans_serif(self, _fp):
        _install_matplotlib_stub(addfont_side_effect=RuntimeError("no"), ttflist=[SimpleNamespace(name="DejaVu Sans")])
        assert font_utils.setup_matplotlib_cjk() == "sans-serif"

    @patch("font_utils.get_cjk_font_path", return_value=None)
    def test_no_font_path_scans_system(self, _fp):
        _install_matplotlib_stub(ttflist=[SimpleNamespace(name="Microsoft YaHei")])
        assert font_utils.setup_matplotlib_cjk() == "Microsoft YaHei"


class TestSetupReportlabCjk:
    @patch("font_utils.get_cjk_font_path", return_value="C:/x.ttf")
    def test_registers_and_returns_cjk(self, _fp):
        pdfmetrics, ttfonts, _cidfonts = _install_reportlab_stub()
        assert font_utils.setup_reportlab_cjk() == "CJK"
        pdfmetrics.registerFont.assert_called_once_with(ttfonts.TTFont("CJK", "C:/x.ttf"))

    @patch("font_utils.get_cjk_font_path", return_value="C:/x.ttf")
    def test_register_error_uses_cid_fallback(self, _fp):
        pdfmetrics, _ttfonts, cidfonts = _install_reportlab_stub(
            register_side_effect=[RuntimeError("no"), None]
        )
        assert font_utils.setup_reportlab_cjk() == "STSong-Light"
        assert pdfmetrics.registerFont.call_args_list[1].args == (cidfonts.UnicodeCIDFont("STSong-Light"),)

    @patch("font_utils.get_cjk_font_path", return_value=None)
    def test_no_font_uses_cid_fallback(self, _fp):
        _pdfmetrics, _ttfonts, cidfonts = _install_reportlab_stub()
        assert font_utils.setup_reportlab_cjk() == "STSong-Light"
        cidfonts.UnicodeCIDFont.assert_called_once_with("STSong-Light")

    @patch("font_utils.get_cjk_font_path", return_value=None)
    def test_cid_fallback_error_returns_helvetica(self, _fp):
        _pdfmetrics, _ttfonts, cidfonts = _install_reportlab_stub()
        cidfonts.UnicodeCIDFont.side_effect = RuntimeError("no CID font")

        assert font_utils.setup_reportlab_cjk() == "Helvetica"

# --- The doubles installed above are scoped to this module --------------------------
# Registering fakes in ``sys.modules`` is what lets the modules under test import
# without their real collaborators, but a hand-built ``types.ModuleType`` has no
# ``__file__`` and no package ``__path__``. Left in place it makes every later
# ``from <package>.<module> import ...`` in the same run fail with
# "ModuleNotFoundError: ... '<package>' is not a package", so the registrations are
# dropped again as soon as this module finishes importing. ``setup_module`` replays
# them for this module's own tests, which keeps patching by module path working, and
# ``teardown_module`` takes them back out. Entries that were already present are
# restored to their previous value rather than deleted.

import types as _scope_types
from unittest.mock import Mock as _scope_mock_base


def _is_import_double(module):
    """Tell a hand-made test double from a genuinely imported module."""
    if isinstance(module, _scope_mock_base):
        return True
    if type(module) is not _scope_types.ModuleType:
        return False
    if getattr(module, "__file__", None):
        return False
    spec = getattr(module, "__spec__", None)
    origin = getattr(spec, "origin", None) if spec is not None else None
    return not origin or origin in ("namespace", "built-in", "frozen")


def _collect_import_doubles(snapshot):
    """Map every double this module installed to the entry it displaced."""
    doubles = {}
    for name, module in list(sys.modules.items()):
        if snapshot.get(name) is module:
            continue
        if _is_import_double(module):
            doubles[name] = (snapshot.get(name), module)
    return doubles


_IMPORT_DOUBLES = _collect_import_doubles(_STUB_MODULE_SNAPSHOT)
_SAVED_IMPORT_MODULES = {}


def _install_import_doubles():
    """Register this module's doubles, returning what has to be put back."""
    saved = {}
    for name, (_previous, double) in _IMPORT_DOUBLES.items():
        saved[name] = sys.modules.get(name)
        sys.modules[name] = double
    return saved


def _restore_modules(saved):
    """Undo :func:`_install_import_doubles`, deleting entries that were absent."""
    for name, previous in saved.items():
        if previous is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = previous


def _eject_import_doubles():
    """Take the doubles out again once this module has finished importing."""
    for name, (previous, _double) in _IMPORT_DOUBLES.items():
        if previous is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = previous


def setup_module(_module=None):
    """Re-register the doubles for the duration of this module's tests."""
    global _SAVED_IMPORT_MODULES
    _SAVED_IMPORT_MODULES = _install_import_doubles()


def teardown_module(_module=None):
    """Drop the doubles again so the next module sees the real packages."""
    _restore_modules(_SAVED_IMPORT_MODULES)


_eject_import_doubles()
