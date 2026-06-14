"""Backward-compatible import for the shared calendar event resolver.

Calendar logic is shared across single-asset backtests and future multi-asset
portfolio/rebalance workflows.  New code should import from
``utils.calendar_events`` directly; this module remains for existing backtester
call sites and tests.
"""

from utils.calendar_events import (  # noqa: F401
    CalendarConditionMaterializer,
    CalendarEventResolver,
    WEEKDAY_ALIASES,
    WEEKDAY_NAMES,
)
