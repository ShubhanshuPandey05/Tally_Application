"""Authentication endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Header, Request, status
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from ...db.models import Membership
from ...services.audit import record
from ...services.entitlements import count_companies, count_users
from ..deps import AuthServiceDep, PrincipalDep, SessionDep
from ..schemas import (
    LoginRequest,
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
    user_agent: Annotated[str | None, Header()] = None,
) -> TokenResponse:
    user, org = await auth.register(
        email=payload.email,
        password=payload.password,
        full_name=payload.full_name,
        org_name=payload.org_name,
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
    tokens = await auth.issue_tokens(
        user, membership, device_name=payload.device_name, user_agent=user_agent
    )
    await record(
        session,
        action="auth.login",
        org_id=membership.org_id,
        user_id=user.id,
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


@router.get("/me", response_model=UserResponse)
async def me(principal: PrincipalDep, session: SessionDep) -> UserResponse:
    membership = await session.scalar(
        select(Membership)
        .where(
            Membership.user_id == principal.user.id,
            Membership.org_id == principal.org_id,
        )
        .options(selectinload(Membership.organisation))
    )
    return UserResponse(
        id=principal.user.id,
        email=principal.user.email,
        full_name=principal.user.full_name,
        org_id=principal.org_id,
        org_name=membership.organisation.name if membership else "",
        role=principal.role,
        must_change_password=principal.user.must_change_password,
        # Two counts on a call the app makes once per cold start. The
        # alternative -- a separate subscription endpoint -- is a second round
        # trip and a window in which the app's idea of the role and its idea of
        # the entitlement disagree.
        subscription=SubscriptionResponse.build(
            principal.entitlement,
            users_used=await count_users(session, principal.org_id),
            companies_used=await count_companies(session, principal.org_id),
        ),
    )
