"""Layer Order Plus — centralised logging.

Provides:
- _log(msg, level) — always logs to QgsMessageLog (LayerOrderPlus tab)
- _vlog(msg) — verbose logging, only active when verbose mode is ON
- set_verbose(enabled) — toggle verbose mode (called by the View checkbox)
- is_verbose() — query current state

Verbose mode is OFF by default. When ON, every method in every class
logs its entry/exit via _vlog. This is essential for debugging the
concurrent-action issues during drag-drop.

The Model (Qt-free) uses _vlog too — it falls back to print() when
QgsMessageLog isn't available (e.g., in unit tests).
"""
import traceback as _traceback

try:
    from qgis.core import QgsMessageLog, Qgis
    _HAS_QGIS = True
except Exception:
    _HAS_QGIS = False

LOG_TAG = "LayerOrderPlus"
_verbose = False


def set_verbose(enabled: bool) -> None:
    """Toggle verbose logging (called by the View's checkbox)."""
    global _verbose
    _verbose = bool(enabled)
    _log(f"Verbose logging {'ON' if _verbose else 'OFF'}")


def is_verbose() -> bool:
    return _verbose


def _log(msg, level=None) -> None:
    """Always log to QgsMessageLog (LayerOrderPlus tab)."""
    if level is None:
        level = Qgis.Info if _HAS_QGIS else 0
    if _HAS_QGIS:
        try:
            QgsMessageLog.logMessage(str(msg), LOG_TAG, level)
            return
        except Exception:
            pass
    # Fallback (tests or very early load)
    try:
        print(f"[{LOG_TAG}] {msg}")
    except Exception:
        pass


def _vlog(msg) -> None:
    """Verbose log — only emits when verbose mode is ON."""
    if not _verbose:
        return
    _log(msg)


def _vlog_method(name: str) -> None:
    """Verbose log a method entry. Call at the top of every method."""
    if not _verbose:
        return
    _log(f"→ {name}")


def _vlog_method_exit(name: str) -> None:
    """Verbose log a method exit."""
    if not _verbose:
        return
    _log(f"← {name}")


def _vlog_error(name: str, exc: Exception) -> None:
    """Log an exception with full traceback."""
    level = Qgis.Critical if _HAS_QGIS else 0
    _log(f"{name} FAILED: {exc!r}", level)
    _log(_traceback.format_exc(), level)
