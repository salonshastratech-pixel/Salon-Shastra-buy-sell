"""Structured JSON logs to stdout (systemd/journald collects them on the VPS). No PII in log lines."""
import json
import logging
import sys

_STD = set(vars(logging.LogRecord("", 0, "", 0, "", (), None))) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def format(self, record):
        out = {"ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"), "level": record.levelname,
               "logger": record.name, "msg": record.getMessage()}
        out.update({k: v for k, v in vars(record).items() if k not in _STD})
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        return json.dumps(out, default=str)


def configure(level: str = "INFO") -> None:
    root = logging.getLogger("teamos")
    if getattr(root, "_configured", False):
        return
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(JsonFormatter())
    root.addHandler(h)
    root.setLevel(level)
    root.propagate = False
    root._configured = True
