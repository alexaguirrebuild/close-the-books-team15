"""What went wrong and which files were read: nothing is swallowed silently."""
from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field

logger = logging.getLogger("bankcheck")

INFO, WARN, ERROR = "INFO", "WARN", "ERROR"
LOG_LEVELS = {INFO: logging.INFO, WARN: logging.WARNING, ERROR: logging.ERROR}
MAX_LOGGED_PER_CODE = 20


@dataclass(frozen=True)
class Issue:
    severity: str
    code: str
    source_type: str
    detail: str
    segment: str | None = None
    file: str | None = None
    line: int | None = None
    count: int = 1


@dataclass(frozen=True)
class FileRecord:
    path: str
    source_type: str
    segment: str | None
    size_bytes: int
    rows: int
    status: str
    error: str | None = None


@dataclass
class IssueLog:
    """Collects issues; each one is logged as it happens, capped per code to keep the log readable."""

    items: list[Issue] = field(default_factory=list)
    _logged: Counter = field(default_factory=Counter)

    def add(self, issue: Issue) -> None:
        self.items.append(issue)
        self._logged[issue.code] += 1
        if self._logged[issue.code] > MAX_LOGGED_PER_CODE:
            return
        where = f" [{issue.file}{f':{issue.line}' if issue.line else ''}]" if issue.file else ""
        times = f" (x{issue.count})" if issue.count > 1 else ""
        logger.log(LOG_LEVELS[issue.severity], "%s %s/%s%s%s: %s", issue.code, issue.source_type, issue.segment or "-", where, times, issue.detail)

    def info(self, code: str, source_type: str, detail: str, **where) -> None:
        self.add(Issue(INFO, code, source_type, detail, **where))

    def warn(self, code: str, source_type: str, detail: str, **where) -> None:
        self.add(Issue(WARN, code, source_type, detail, **where))

    def error(self, code: str, source_type: str, detail: str, **where) -> None:
        self.add(Issue(ERROR, code, source_type, detail, **where))

    def totals(self) -> dict[str, int]:
        """Number of affected records per severity."""
        counts: Counter = Counter()
        for issue in self.items:
            counts[issue.severity] += issue.count
        return {severity: counts.get(severity, 0) for severity in (ERROR, WARN, INFO)}

    @property
    def has_errors(self) -> bool:
        return any(issue.severity == ERROR for issue in self.items)


@dataclass
class Manifest:
    files: list[FileRecord] = field(default_factory=list)

    def add(self, record: FileRecord) -> None:
        self.files.append(record)
