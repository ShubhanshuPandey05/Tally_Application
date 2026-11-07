"""Sending email, over plain SMTP.

There was no email in this product at all until password reset needed it,
which is why an admin hands a new colleague a temporary password in person.
SMTP rather than one provider's HTTP API, so the provider is a line in the
deployment config: Amazon SES, Resend and Brevo all accept it.

The standard library's ``smtplib`` is blocking, so each send runs on a worker
thread. Sends happen after the response has gone (see the password-reset
route), so a slow provider never shows up as a slow button -- and never as a
timing difference between an address that has an account and one that does
not.
"""

from __future__ import annotations

import asyncio
import logging
import smtplib
import ssl
from collections.abc import Awaitable, Callable
from email.message import EmailMessage

from ..config import Settings

logger = logging.getLogger(__name__)

#: One message out. Swappable so tests can read what would have been sent.
Transport = Callable[[EmailMessage], Awaitable[None]]


class Mailer:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self.transport: Transport = self._smtp_send

    @property
    def enabled(self) -> bool:
        return self._settings.email_enabled

    async def send(self, *, to: str, subject: str, text: str) -> bool:
        """Send one plain-text message. True when the provider accepted it.

        Never raises: a provider outage is logged and reported as False, and
        the person sees the same "check your email" either way -- the screen
        cannot say whether an address has an account, so it cannot say that a
        message to it failed either.
        """
        if not self.enabled:
            logger.warning("email is not configured; not sending %r", subject)
            return False
        message = EmailMessage()
        message["From"] = self._settings.smtp_from
        message["To"] = to
        message["Subject"] = subject
        message.set_content(text)
        try:
            await self.transport(message)
        except Exception:
            logger.exception("could not send email %r", subject)
            return False
        return True

    async def _smtp_send(self, message: EmailMessage) -> None:
        await asyncio.to_thread(self._smtp_send_blocking, message)

    def _smtp_send_blocking(self, message: EmailMessage) -> None:
        s = self._settings
        host, port, timeout = s.smtp_host or "", s.smtp_port, s.smtp_timeout_seconds
        context = ssl.create_default_context()
        if s.smtp_security == "ssl":
            server: smtplib.SMTP = smtplib.SMTP_SSL(
                host, port, timeout=timeout, context=context
            )
        else:
            server = smtplib.SMTP(host, port, timeout=timeout)
        with server:
            if s.smtp_security == "starttls":
                server.starttls(context=context)
            if s.smtp_username:
                server.login(s.smtp_username, s.smtp_password or "")
            server.send_message(message)
