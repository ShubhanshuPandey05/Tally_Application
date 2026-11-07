"""Authentication endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Header, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ...config import Settings
from ...core.errors import NotConfigured
from ...db.models import Membership, Role, User
from ...services import trial
from ...services.audit import record
from ...services.entitlements import Entitlement, count_companies, count_users
from ...services.password_reset import PasswordResetService, reset_email
from ..deps import (
    AuthServiceDep,
    MailerDep,
    PhoneVerifierDep,
    PrincipalDep,
    SessionDep,
    SettingsDep,
)
from ..schemas import (
    AddPhoneRequest,
    LoginRequest,
    PartnerCodeCheckRequest,
    PartnerCodeCheckResponse,
    PasswordResetConfirm,
    PasswordResetRequest,
    RefreshRequest,
    RegisterRequest,
    SubscriptionResponse,
    TokenResponse,
    UserResponse,
)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(
    payload: RegisterRequest,
    request: Request,
    auth: AuthServiceDep,
    session: SessionDep,
    verifier: PhoneVerifierDep,
    user_agent: Annotated[str | None, Header()] = None,
) -> TokenResponse:
    user, org = await auth.register(
        email=payload.email,
        password=payload.password,
        full_name=payload.full_name,
        org_name=payload.org_name,
        phone=payload.phone,
        phone_proof=payload.phone_token,
        verifier=verifier,
        partner_code=payload.partner_code,
    )
    membership = await auth.primary_membership(user)
    tokens = await auth.issue_tokens(
        user, membership, device_name=payload.device_name, user_agent=user_agent
    )
    await record(
        session,
        action="auth.register",
        org_id=org.id,
        user_id=user.id,
        detail={
            "trial": org.trial_granted_at is not None,
            "partner_id": org.partner_id,
            "phone_verified": user.phone_verified_at is not None,
        },
        request=request,
    )
    return TokenResponse(**tokens.__dict__)


@router.post("/login", response_model=TokenResponse)
async def login(
    payload: LoginRequest,
    request: Request,
    auth: AuthServiceDep,
    session: SessionDep,
    user_agent: Annotated[str | None, Header()] = None,
) -> TokenResponse:
    user = await auth.authenticate(email=payload.email, password=payload.password)
    membership = await auth.primary_membership(user)
    # For a business that signed up before trials existed, or on a build that
    # did not ask for a phone: the first admin sign-in with a number on file is
    # when its trial begins.
    trial_granted = await auth.grant_trial_on_sign_in(user, membership)
    tokens = await auth.issue_tokens(
        user, membership, device_name=payload.device_name, user_agent=user_agent
    )
    await record(
        session,
        action="auth.login",
        org_id=membership.org_id,
        user_id=user.id,
        detail={"trial_granted": True} if trial_granted else None,
        request=request,
    )
    return TokenResponse(**tokens.__dict__)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    payload: RefreshRequest,
    auth: AuthServiceDep,
    user_agent: Annotated[str | None, Header()] = None,
) -> TokenResponse:
    tokens = await auth.rotate_refresh_token(payload.refresh_token, user_agent=user_agent)
    return TokenResponse(**tokens.__dict__)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(payload: RefreshRequest, auth: AuthServiceDep) -> None:
    # Deliberately unauthenticated: a client whose access token has already
    # expired must still be able to clean up its refresh token, and presenting
    # the token is itself proof enough to revoke that one token.
    await auth.revoke(payload.refresh_token)


@router.post("/logout-all", status_code=status.HTTP_204_NO_CONTENT)
async def logout_all(
    principal: PrincipalDep, auth: AuthServiceDep, session: SessionDep, request: Request
) -> None:
    count = await auth.revoke_all(principal.user.id)
    await record(
        session,
        action="auth.logout_all",
        org_id=principal.org_id,
        user_id=principal.user.id,
        detail={"sessions_revoked": count},
        request=request,
    )


@router.post("/partner-code", response_model=PartnerCodeCheckResponse)
async def check_partner_code(
    payload: PartnerCodeCheckRequest, session: SessionDep
) -> PartnerCodeCheckResponse:
    """Is this a partner code? Asked by the signup form as the box is typed in.

    Unauthenticated, because the person typing has no account yet, and rate
    limited in a bucket of its own (``RateLimitMiddleware``) so that guessing
    codes is slow without the live check eating into anybody's sign-in
    allowance. A POST rather than a GET so the code stays out of access logs
    and proxy caches.
    """
    partner = await trial.find_partner_by_code(session, payload.code)
    if partner is None:
        return PartnerCodeCheckResponse(valid=False)
    return PartnerCodeCheckResponse(
        valid=True, partner_name=partner.full_name or None
    )


@router.post("/password-reset", status_code=status.HTTP_202_ACCEPTED)
async def request_password_reset(
    payload: PasswordResetRequest,
    session: SessionDep,
    settings: SettingsDep,
    mailer: MailerDep,
    background: BackgroundTasks,
    request: Request,
) -> dict:
    """Email a reset code to an account, if there is one.

    The answer is the same whether or not the address has an account, and the
    email goes out after the response, so neither the words nor the time taken
    can tell a stranger who is a customer. Rate limited with sign-in
    (``RateLimitMiddleware``), on top of the per-account limits in the service.
    """
    if not mailer.enabled:
        raise NotConfigured("email is not configured")

    to_send = await PasswordResetService(session, settings).issue(payload.email)
    if to_send is not None:
        await record(
            session,
            action="auth.password_reset_requested",
            org_id=await _first_org_id(session, to_send.user_id),
            user_id=to_send.user_id,
            request=request,
        )
        subject, text = reset_email(to_send, settings.password_reset_code_minutes)
        background.add_task(mailer.send, to=to_send.email, subject=subject, text=text)
    return {"status": "sent"}


@router.post("/password-reset/confirm", status_code=status.HTTP_204_NO_CONTENT)
async def confirm_password_reset(
    payload: PasswordResetConfirm,
    session: SessionDep,
    settings: SettingsDep,
    request: Request,
) -> None:
    """Set a new password with an emailed code. Every device is signed out;
    the app signs this one straight back in with the new password."""
    user = await PasswordResetService(session, settings).reset(
        email=payload.email, code=payload.code, new_password=payload.new_password
    )
    await record(
        session,
        action="auth.password_reset",
        org_id=await _first_org_id(session, user.id),
        user_id=user.id,
        request=request,
    )


async def _first_org_id(session: AsyncSession, user_id: str) -> str | None:
    # For the audit row only: a reset belongs to the person, not to one of
    # their businesses, but the trail is read per business.
    return await session.scalar(
        select(Membership.org_id)
        .where(Membership.user_id == user_id)
        .order_by(Membership.created_at)
        .limit(1)
    )


@router.post("/me/phone", response_model=UserResponse)
async def add_phone(
    payload: AddPhoneRequest,
    principal: PrincipalDep,
    session: SessionDep,
    settings: SettingsDep,
    auth: AuthServiceDep,
    verifier: PhoneVerifierDep,
    request: Request,
) -> UserResponse:
    """Set the signed-in person's mobile number, and start the trial if it was waiting.

    Not gated on the subscription: an account that cannot do anything else
    yet is exactly the one this is for.
    """
    user = principal.user
    phone, verified_at = await trial.accept_phone(
        session,
        raw=payload.phone,
        proof=payload.phone_token,
        verifier=verifier,
        settings=settings,
        user_id=user.id,
    )
    if phone != user.phone or verified_at is not None:
        # A proof belongs to one number: a new number starts unproven unless
        # it came with a proof of its own.
        user.phone_verified_at = verified_at
    user.phone = phone

    membership = await auth.primary_membership(user)
    org = membership.organisation
    attached_partner = None
    # Only an admin speaks for the business, and a partner is attached once:
    # an account that already has one keeps it, whatever is typed here.
    if (
        membership.role is Role.ADMIN
        and org.partner_id is None
        and trial.normalise_partner_code(payload.partner_code) is not None
    ):
        partner = await trial.resolve_partner_code(session, payload.partner_code)
        if partner is not None:
            org.partner_id = partner.id
            attached_partner = partner.id
    trial_granted = await auth.grant_trial_on_sign_in(user, membership)
    await session.flush()

    await record(
        session,
        action="auth.add_phone",
        org_id=principal.org_id,
        user_id=user.id,
        detail={
            "trial_granted": trial_granted,
            "partner_id": attached_partner,
            "phone_verified": user.phone_verified_at is not None,
        },
        request=request,
    )
    # The principal's entitlement was read before the trial was granted, so
    # the answer is built from the organisation as it now stands.
    return await _user_response(
        session, settings, user, principal.org_id, principal.role, Entitlement.of(org)
    )


@router.get("/me", response_model=UserResponse)
async def me(
    principal: PrincipalDep, session: SessionDep, settings: SettingsDep
) -> UserResponse:
    return await _user_response(
        session,
        settings,
        principal.user,
        principal.org_id,
        principal.role,
        principal.entitlement,
    )


async def _user_response(
    session: AsyncSession,
    settings: Settings,
    user: User,
    org_id: str,
    role: Role,
    entitlement: Entitlement,
) -> UserResponse:
    membership = await session.scalar(
        select(Membership)
        .where(
            Membership.user_id == user.id,
            Membership.org_id == org_id,
        )
        .options(selectinload(Membership.organisation))
    )
    organisation = membership.organisation if membership else None
    return UserResponse(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        org_id=org_id,
        org_name=organisation.name if organisation else "",
        role=role,
        must_change_password=user.must_change_password,
        phone=user.phone,
        phone_verified=user.phone_verified_at is not None,
        # Nobody signs in to the demo any more, but a disabled demo user that
        # somehow did must not be sent to a form it cannot usefully fill in.
        needs_phone=user.phone is None and not (organisation and organisation.is_demo),
        # Two counts on a call the app makes once per cold start. The
        # alternative -- a separate subscription endpoint -- is a second round
        # trip and a window in which the app's idea of the role and its idea of
        # the entitlement disagree.
        subscription=SubscriptionResponse.build(
            entitlement,
            users_used=await count_users(session, org_id),
            companies_used=await count_companies(session, org_id),
            trial_days=settings.trial_days,
        ),
    )
