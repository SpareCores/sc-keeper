from asyncio import CancelledError


def before_send(event, hint):
    """Do not report intended shutdown errors to sentry."""
    exc_info = hint.get("exc_info")
    if exc_info:
        exc_type, exc_value, tb = exc_info
        ignore_exc_types = (
            CancelledError,
            KeyboardInterrupt,
            SystemExit,
        )
        if issubclass(exc_type, ignore_exc_types):
            return None
    return event
