"""Managing the people in one organisation.

The shape of this is set by one product rule: **a colleague does not sign
themselves up.** A shop owner adds their staff, decides which sets of books each
one may open, and hands them a password. Self-registration creates a brand new
organisation with its own connector and its own companies, which is the opposite
of what "add my two staff members" means.

There is no email delivery in this system, so an invitation link is not
available. A created account therefore comes with a temporary password that is
shown to the admin exactly once -- the same pattern, and the same reasoning, as
the connector pairing secret. ``must_change_password`` is what stops the admin
knowing their colleague's password indefinitely.

Access is deny-by-default and asymmetric:

    admin  ->  every company in the organisation, by virtue of the role
    staff  ->  only the companies granted in ``company_access``

Enforcement lives in ``deps.get_company``, not here. This module writes the
grants; the dependency every company-scoped route already passes through is what
makes them mean something.
"""

from __future__ import annotations

from fastapi import APIRouter, Request, status
from sqlalchemy import delete, select

from ...core.errors import ConflictError, NotFound, PermissionDenied
from ...core.security import generate_temporary_password, hash_password, verify_password
from ...db.models import Company, CompanyAccess, Membership, Organisation, Role, User
from ...services.audit import record
from ...services.entitlements import check_user_limit
from ..deps import PrincipalDep, SessionDep
from ..schemas import (
    ChangePasswordRequest,
    CreateMemberRequest,
    MemberCreatedResponse,
    MemberResponse,
    ResetMemberPasswordResponse,
    UpdateMemberRequest,
)

router = APIRouter(prefix="/team", tags=["team"])


async def _company_ids_for(session, principal_org_id: str, user: User, role: Role) -> list[str]:
    """The companies this member can actually open.

    An admin's list is every company in the org. Returning their real reach
    rather than an empty list keeps the "admin means all" rule in one place --
    here -- instead of restating it in the app, where the two could drift.
    """
    if role.allows(Role.ADMIN):
        rows = await session.execute(
            select(Company.id).where(
                Company.org_id == principal_org_id, Company.is_active.is_(True)
            )
        )
        return list(rows.scalars().all())

    rows = await session.execute(
        select(CompanyAccess.company_id).where(CompanyAccess.user_id == user.id)
    )
    return list(rows.scalars().all())


async def _member_response(session, org_id: str, user: User, role: Role) -> MemberResponse:
    return MemberResponse(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        role=role,
        is_active=user.is_active,
        must_change_password=user.must_change_password,
        last_login_at=user.last_login_at,
        company_ids=await _company_ids_for(session, org_id, user, role),
    )


async def _set_company_access(
    session, *, org_id: str, member: User, company_ids: list[str], granted_by: str
) -> None:
    """Replace a staff member's grants with exactly ``company_ids``.

    Every id is checked against the caller's own organisation before it is
    written. Without that, an admin could grant their staff access to a company
    id belonging to another customer simply by sending it -- the request body is
    not a trusted source of ids just because the caller is an admin.
    """
    wanted = set(company_ids)
    if wanted:
        rows = await session.execute(
            select(Company.id).where(Company.org_id == org_id, Company.id.in_(wanted))
        )
        valid = set(rows.scalars().all())
        unknown = wanted - valid
        if unknown:
            raise NotFound(
                f"companies not in org {org_id}: {sorted(unknown)}",
                user_message="One of those companies was not found.",
            )
        wanted = valid

    # Replaced wholesale rather than merged: the screen sends the full set of
    # ticked boxes, so anything absent is an un-tick, not an omission.
    await session.execute(delete(CompanyAccess).where(CompanyAccess.user_id == member.id))
    for company_id in sorted(wanted):
        session.add(
            CompanyAccess(user_id=member.id, company_id=company_id, granted_by=granted_by)
        )


# --------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------


@router.get("", response_model=list[MemberResponse])
async def list_members(principal: PrincipalDep, session: SessionDep) -> list[MemberResponse]:
    """Everyone in the caller's organisation.

    Admin-only. A staff member has no reason to enumerate their colleagues, and
    the list carries who is an admin -- which is a map of who to target.
    """
    principal.require(Role.ADMIN)

    rows = await session.execute(
        select(User, Membership.role)
        .join(Membership, Membership.user_id == User.id)
        .where(Membership.org_id == principal.org_id)
        .order_by(User.created_at)
    )
    return [
        await _member_response(session, principal.org_id, user, role)
        for user, role in rows.all()
    ]


# --------------------------------------------------------------------------
# Writing
# --------------------------------------------------------------------------


@router.post("/me/password", status_code=status.HTTP_204_NO_CONTENT)
async def change_own_password(
    payload: ChangePasswordRequest,
    principal: PrincipalDep,
    session: SessionDep,
    request: Request,
) -> None:
    """Set a new password for the signed-in account.

    Any role: this is the path off a temporary password, and staff are exactly
    the people who arrive on one.

    Declared before ``/{member_id}/...`` deliberately. FastAPI matches in
    declaration order, so with the parameterised route first this one is never
    reached -- ``me`` is captured as a member id and the caller gets a 404, or a
    403 if they are staff. It looks like a permissions bug and is a routing one.
    """
    user = principal.user

    # Skipped only while the account is still on an admin-issued password. In
    # that state the current password proves nothing about who is holding the
    # phone -- someone else chose it -- and demanding it would just be a step.
    # The demo's password is published and shared. One visitor changing it
    # would lock out everybody else.
    principal.require_mutable()

    proven = payload.current_password is not None and verify_password(
        payload.current_password or "", user.password_hash
    )
    if not user.must_change_password and not proven:
        raise PermissionDenied(
            "current password does not match",
            user_message="That is not your current password.",
        )

    user.password_hash = hash_password(payload.new_password)
    user.must_change_password = False
    await session.flush()

    await record(
        session,
        action="user.change_password",
        org_id=principal.org_id,
        user_id=user.id,
        detail={},
        request=request,
    )


@router.post("", response_model=MemberCreatedResponse, status_code=status.HTTP_201_CREATED)
async def create_member(
    payload: CreateMemberRequest,
    principal: PrincipalDep,
    session: SessionDep,
    request: Request,
) -> MemberCreatedResponse:
    """Add a colleague, and hand back their temporary password once."""
    principal.require(Role.ADMIN)
    await check_user_limit(session, principal.org)

    email = payload.email.strip().lower()
    existing = await session.scalar(select(User).where(User.email == email))

    if existing is not None:
        # A person can legitimately belong to two organisations -- an accountant
        # serving several businesses is the obvious case -- so an existing
        # account is only a conflict when they are already in *this* one.
        already = await session.scalar(
            select(Membership).where(
                Membership.user_id == existing.id, Membership.org_id == principal.org_id
            )
        )
        if already is not None:
            raise ConflictError(
                f"{email} is already a member of {principal.org_id}",
                user_message="That person is already on your team.",
            )
        raise ConflictError(
            f"{email} already has an account elsewhere",
            user_message=(
                "That email already has a TallyFlow account. Ask them to sign in "
                "and contact support to be added to this business."
            ),
        )

    password = generate_temporary_password()
    member = User(
        email=email,
        password_hash=hash_password(password),
        full_name=payload.full_name,
        must_change_password=True,
    )
    session.add(member)
    await session.flush()

    session.add(
        Membership(user_id=member.id, org_id=principal.org_id, role=payload.role)
    )

    if not payload.role.allows(Role.ADMIN):
        await _set_company_access(
            session,
            org_id=principal.org_id,
            member=member,
            company_ids=payload.company_ids,
            granted_by=principal.user.id,
        )
    await session.flush()

    await record(
        session,
        action="team.create_member",
        org_id=principal.org_id,
        user_id=principal.user.id,
        detail={
            "member_id": member.id,
            "email": email,
            "role": payload.role.value,
            "companies": len(payload.company_ids),
        },
        request=request,
    )

    return MemberCreatedResponse(
        member=await _member_response(session, principal.org_id, member, payload.role),
        temporary_password=password,
    )


@router.patch("/{member_id}", response_model=MemberResponse)
async def update_member(
    member_id: str,
    payload: UpdateMemberRequest,
    principal: PrincipalDep,
    session: SessionDep,
    request: Request,
) -> MemberResponse:
    """Change a colleague's role, their company access, or switch them off."""
    principal.require(Role.ADMIN)
    principal.require_changes()
    member, membership = await _member_in_org(session, principal.org_id, member_id)

    role = payload.role or membership.role

    # Two guards against an organisation locking itself out. They are separate
    # checks because they fail differently: the first is an admin demoting or
    # disabling *themselves* by accident, the second is the last admin being
    # removed by another admin. Either one leaves nobody who can add a PC, link a
    # company, or restore access -- and no self-service way back.
    losing_admin = membership.role.allows(Role.ADMIN) and (
        not role.allows(Role.ADMIN) or payload.is_active is False
    )
    if losing_admin and await _admin_count(session, principal.org_id) <= 1:
        raise ConflictError(
            f"org {principal.org_id} would have no admin",
            user_message=(
                "This is the only admin. Make someone else an admin first, "
                "otherwise nobody could add a PC or manage the team."
            ),
        )

    if payload.full_name is not None:
        member.full_name = payload.full_name
    if payload.is_active is not None:
        member.is_active = payload.is_active
    membership.role = role

    if role.allows(Role.ADMIN):
        # Promotion to admin clears the grants rather than keeping them dormant.
        # An admin's access comes from the role, so leaving stale rows behind
        # would silently restore a half-forgotten list if they were ever demoted.
        await session.execute(delete(CompanyAccess).where(CompanyAccess.user_id == member.id))
    elif payload.company_ids is not None:
        await _set_company_access(
            session,
            org_id=principal.org_id,
            member=member,
            company_ids=payload.company_ids,
            granted_by=principal.user.id,
        )
    await session.flush()

    await record(
        session,
        action="team.update_member",
        org_id=principal.org_id,
        user_id=principal.user.id,
        detail={
            "member_id": member.id,
            "role": role.value,
            "is_active": member.is_active,
            "companies": None if payload.company_ids is None else len(payload.company_ids),
        },
        request=request,
    )
    return await _member_response(session, principal.org_id, member, role)


@router.post("/{member_id}/password", response_model=ResetMemberPasswordResponse)
async def reset_member_password(
    member_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    request: Request,
) -> ResetMemberPasswordResponse:
    """Issue a new temporary password. The answer to "I forgot mine"."""
    principal.require(Role.ADMIN)
    principal.require_mutable()
    member, _ = await _member_in_org(session, principal.org_id, member_id)

    password = generate_temporary_password()
    member.password_hash = hash_password(password)
    member.must_change_password = True
    await session.flush()

    await record(
        session,
        action="team.reset_password",
        org_id=principal.org_id,
        user_id=principal.user.id,
        detail={"member_id": member.id},
        request=request,
    )
    return ResetMemberPasswordResponse(temporary_password=password)


@router.delete("/{member_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(
    member_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    request: Request,
) -> None:
    """Remove someone from this organisation.

    Deletes the membership and the grants, never the user: the same person may
    belong to another business, and their audit history here has to keep
    resolving to a real account.
    """
    principal.require(Role.ADMIN)
    principal.require_mutable()
    if member_id == principal.user.id:
        raise ConflictError(
            "cannot remove yourself",
            user_message="You cannot remove yourself from the team.",
        )

    member, membership = await _member_in_org(session, principal.org_id, member_id)
    if membership.role.allows(Role.ADMIN) and await _admin_count(session, principal.org_id) <= 1:
        raise ConflictError(
            f"org {principal.org_id} would have no admin",
            user_message="This is the only admin. Make someone else an admin first.",
        )

    await session.execute(delete(CompanyAccess).where(CompanyAccess.user_id == member.id))
    await session.delete(membership)

    await record(
        session,
        action="team.remove_member",
        org_id=principal.org_id,
        user_id=principal.user.id,
        detail={"member_id": member.id, "email": member.email},
        request=request,
    )


# --------------------------------------------------------------------------


async def _member_in_org(session, org_id: str, member_id: str) -> tuple[User, Membership]:
    """Resolve a member, or 404 -- never confirming an id outside the org."""
    membership = await session.scalar(
        select(Membership).where(
            Membership.user_id == member_id, Membership.org_id == org_id
        )
    )
    if membership is None:
        raise NotFound(
            f"user {member_id} is not in org {org_id}",
            user_message="That person was not found on your team.",
        )
    member = await session.get(User, member_id)
    if member is None:
        raise NotFound(f"user {member_id}", user_message="That person was not found.")
    return member, membership


async def _admin_count(session, org_id: str) -> int:
    rows = await session.execute(
        select(Membership.user_id)
        .join(User, User.id == Membership.user_id)
        .where(
            Membership.org_id == org_id,
            Membership.role == Role.ADMIN,
            User.is_active.is_(True),
        )
    )
    return len(list(rows.scalars().all()))


async def _org_name(session, org_id: str) -> str:
    org = await session.get(Organisation, org_id)
    return org.name if org else ""
