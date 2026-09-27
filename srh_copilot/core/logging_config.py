"""Structured logging to console and logs/app.log. PII is redacted before it reaches the log.

Only the message and any traceback are redacted, never the timestamp or the logger
name: a date such as 2026-09-27 14:03 must stay readable in the log."""
import logging
import logging.handlers
from pathlib import Path

from config.settings import PROJECT_ROOT, settings
from core.pii import redact_pii


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        if not settings.guardrails_block_pii_in_logs:
            return super().format(record)
        domains = settings.pii_allowed_email_domains
        record = logging.makeLogRecord(record.__dict__)  # never modify the record other handlers see
        record.msg, record.args = redact_pii(record.getMessage(), domains), None
        if record.exc_info and not record.exc_text:
            record.exc_text = self.formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = redact_pii(record.exc_text, domains)
        if record.stack_info:
            record.stack_info = redact_pii(record.stack_info, domains)
        return super().format(record)


def setup_logging() -> None:
    log_dir: Path = PROJECT_ROOT / "logs"
    log_dir.mkdir(exist_ok=True)
    fmt = RedactingFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(settings.log_level.upper())
    if root.handlers:
        return
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    fileh = logging.handlers.RotatingFileHandler(log_dir / "app.log", maxBytes=5_000_000, backupCount=3, encoding="utf-8")
    fileh.setFormatter(fmt)
    root.addHandler(console)
    root.addHandler(fileh)
