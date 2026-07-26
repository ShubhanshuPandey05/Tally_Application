"""Typed errors for the Tally layer.

Each carries a ``user_message`` because CLAUDE.md requires user-friendly errors
all the way to the phone, and a ``retryable`` flag so the connector's queue can
decide between retrying and failing fast without string-matching messages.
"""

from __future__ import annotations


class TallyError(Exception):
    """Base class for every failure originating at or below the connector."""

    code = "tally_error"
    user_message = "Could not reach your Tally data. Please try again."
    retryable = False

    def __init__(self, message: str, *, user_message: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        if user_message is not None:
            self.user_message = user_message

    def to_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "message": self.message,
            "user_message": self.user_message,
            "retryable": self.retryable,
        }


class TallyUnreachableError(TallyError):
    """Tally is not listening -- closed, or ODBC/HTTP gateway disabled."""

    code = "tally_unreachable"
    user_message = (
        "TallyPrime isn't responding on this computer. "
        "Make sure Tally is open and that Gateway of Tally is set to accept requests."
    )
    retryable = True


class TallyTimeoutError(TallyError):
    """Tally accepted the request but did not answer in time.

    Usually means a modal dialog is open in Tally, or the requested report is
    genuinely huge -- both resolve on their own, so this is retryable.
    """

    code = "tally_timeout"
    user_message = "TallyPrime is taking too long to respond. Retrying shortly."
    retryable = True


class TallyBusyError(TallyError):
    """The connector's Tally queue is full.

    Tally serves one request at a time, so the queue is the only place that can
    apply backpressure. Refusing here is kinder than accepting work that would
    sit behind a five-minute export and time out anyway -- the caller learns
    immediately and can fall back to its snapshot.
    """

    code = "tally_busy"
    user_message = "Your Tally PC is working through other requests. Please try again shortly."
    retryable = True


class TallyResponseError(TallyError):
    """Tally answered with an in-band ``<LINEERROR>``."""

    code = "tally_response_error"
    user_message = (
        "TallyPrime rejected this request. "
        "The report may not be available for this company."
    )


class TallyParseError(TallyError):
    """The response could not be decoded even after sanitising."""

    code = "tally_parse_error"
    user_message = "Received an unreadable response from TallyPrime."


class CompanyNotLoadedError(TallyError):
    """The requested company is not open in Tally."""

    code = "company_not_loaded"
    user_message = "That company isn't open in TallyPrime right now. Please open it and try again."
    retryable = True


class UnknownQueryError(TallyError):
    """The connector was asked to run a query name it does not know.

    Signals a version skew between backend and connector; the connector must
    refuse rather than improvise.
    """

    code = "unknown_query"
    user_message = "Your Tally Connector is out of date. Please update it."
