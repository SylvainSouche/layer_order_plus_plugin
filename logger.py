"""Logging for Advanced Layer Order — the standard `logging` module.

Every module logs to a child of the ``AdvancedLayerOrder`` logger
(``logging.getLogger("AdvancedLayerOrder.<module>")``). Records go to the
AdvancedLayerOrder tab of QGIS's Log Messages panel when QGIS is available,
and to stderr otherwise (unit tests, scripts).

INFO and above are always shown; DEBUG ("verbose", toggled by the dock's
checkbox) traces drops, Model mutations and QGIS sync.
"""
import logging

LOG_TAG = "AdvancedLayerOrder"
log = logging.getLogger(LOG_TAG)


class _QgisMessageLogHandler(logging.Handler):
    """Forward records to QgsMessageLog under the AdvancedLayerOrder tag."""

    def emit(self, record: logging.LogRecord) -> None:
        from qgis.core import Qgis, QgsMessageLog
        if record.levelno >= logging.ERROR:
            level = Qgis.Critical
        elif record.levelno >= logging.WARNING:
            level = Qgis.Warning
        else:
            level = Qgis.Info
        QgsMessageLog.logMessage(self.format(record), LOG_TAG, level)


def _install() -> None:
    if log.handlers:
        return  # module reloaded: keep the existing handler
    try:
        import qgis.core  # noqa: F401
        handler: logging.Handler = _QgisMessageLogHandler()
    except ImportError:
        handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("[%(name)s] %(message)s"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    log.propagate = False


def set_verbose(enabled: bool) -> None:
    """Show DEBUG records (True) or only INFO and above (False)."""
    log.setLevel(logging.DEBUG if enabled else logging.INFO)
    log.info("Verbose logging %s", "ON" if enabled else "OFF")


_install()
