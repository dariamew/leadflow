import asyncio
import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import (
    AuthContext,
    CurrentUser,
    Session,
    check_origin,
    hash_password,
    limit_login,
    normalize_username,
    token_hash,
)
from app.models import LoginLimit, PasswordInvitation, User, UserAdminLock, UserSession

Role = Literal["admin", "manager"]


async def administrator(context: CurrentUser) -> AuthContext:
    if context.user.role != "admin":
        raise HTTPException(403, "Administrator required")
    return context


Administrator = Annotated[AuthContext, Depends(administrator)]
router = APIRouter(prefix="/api/v1/users", tags=["users"])
invitation_router = APIRouter(prefix="/api/v1/auth/invitations", tags=["invitations"])


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class UserCreate(StrictModel):
    username: str = Field(min_length=3, max_length=64)
    role: Role = "manager"

    @field_validator("username")
    @classmethod
    def username_format(cls, value: str) -> str:
        return normalize_username(value)


class UserUpdate(StrictModel):
    role: Role | None = None
    is_active: bool | None = None

    @model_validator(mode="after")
    def nonempty(self):
        if self.role is None and self.is_active is None:
            raise ValueError("Provide a role or active status")
        return self


class ManagedUser(BaseModel):
    id: str
    username: str
    role: Role
    is_active: bool
    password_set: bool
    created_at: datetime
    invitation_expires_at: datetime | None


class UserPage(BaseModel):
    items: list[ManagedUser]
    total: int


class InvitationIssued(BaseModel):
    user: ManagedUser
    invitation_url: str
    expires_at: datetime


class InvitationToken(StrictModel):
    token: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$")


class PasswordSetup(InvitationToken):
    password: str = Field(min_length=12, max_length=128)


class InvitationInfo(BaseModel):
    username: str
    expires_at: datetime


def managed_user(user: User, expires_at: datetime | None = None) -> ManagedUser:
    return ManagedUser(
        id=user.id,
        username=user.username,
        role=user.role,
        is_active=user.is_active,
        password_set=user.password_hash is not None,
        created_at=utc_date(user.created_at),
        invitation_expires_at=utc_date(expires_at) if expires_at else None,
    )


def utc_date(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


async def lock_administration(session: AsyncSession, actor: AuthContext) -> None:
    # Serialize mutations, including cross-demotions by two administrators.
    claimed = await session.execute(
        update(UserAdminLock).where(UserAdminLock.id == 1).values(version=UserAdminLock.version + 1)
    )
    if claimed.rowcount != 1:
        raise HTTPException(503, "User administration is not initialized")
    await session.refresh(actor.user)
    alive = await session.scalar(
        select(UserSession.token_hash).where(UserSession.token_hash == actor.session.token_hash)
    )
    if not alive or not actor.user.is_active or actor.user.role != "admin":
        raise HTTPException(403, "Administrator required")


def invitation_origin(request: Request) -> str:
    configured = request.app.state.settings.auth_public_origin
    if configured:
        return configured
    # Production links must use a configured origin, never an arbitrary Host header.
    if request.url.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise HTTPException(503, "Invitation origin not configured")
    return str(request.base_url).rstrip("/")


async def issue_invitation(
    session: AsyncSession,
    user: User,
    request: Request,
    purpose: Literal["setup", "reset"] = "setup",
) -> InvitationIssued:
    origin = invitation_origin(request)
    token = secrets.token_urlsafe(32)
    expires = datetime.now(UTC) + timedelta(hours=request.app.state.settings.auth_invite_hours)
    await session.execute(delete(PasswordInvitation).where(PasswordInvitation.user_id == user.id))
    session.add(
        PasswordInvitation(
            token_hash=token_hash(token), user_id=user.id, expires_at=expires, purpose=purpose
        )
    )
    await session.commit()
    return InvitationIssued(
        user=managed_user(user, expires),
        invitation_url=f"{origin}/#set-password={token}",
        expires_at=expires,
    )


@router.get("", response_model=UserPage)
async def list_users(
    actor: Administrator,
    session: Session,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
):
    rows = await session.execute(
        select(User, PasswordInvitation.expires_at)
        .outerjoin(PasswordInvitation, PasswordInvitation.user_id == User.id)
        .order_by(User.created_at, User.id)
        .offset(offset)
        .limit(limit)
    )
    total = await session.scalar(select(func.count()).select_from(User))
    return UserPage(items=[managed_user(user, expires) for user, expires in rows], total=total)


@router.post("", response_model=InvitationIssued, status_code=201)
async def create_user(
    payload: UserCreate, actor: Administrator, session: Session, request: Request
):
    invitation_origin(request)
    await lock_administration(session, actor)
    user = User(username=payload.username, role=payload.role, password_hash=None)
    session.add(user)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(409, "Username already exists") from None
    return await issue_invitation(session, user, request)


@router.post("/{user_id}/invitation", response_model=InvitationIssued)
async def reissue_invitation(
    user_id: str, actor: Administrator, session: Session, request: Request
):
    await lock_administration(session, actor)
    user = await session.scalar(select(User).where(User.id == user_id).with_for_update())
    if user is None:
        raise HTTPException(404, "User not found")
    if not user.is_active or user.password_hash is not None:
        raise HTTPException(409, "Invitation requires an active pending user")
    return await issue_invitation(session, user, request)


@router.post("/{user_id}/password-reset", response_model=InvitationIssued)
async def password_reset(
    user_id: str,
    actor: Administrator,
    session: Session,
    request: Request,
):
    await lock_administration(session, actor)
    user = await session.scalar(select(User).where(User.id == user_id).with_for_update())
    if user is None:
        raise HTTPException(404, "User not found")
    if user.id == actor.user.id:
        raise HTTPException(409, "Cannot reset your own password through administration")
    if not user.is_active or user.password_hash is None:
        raise HTTPException(409, "Password reset requires an active user with a password")
    # Issuance leaves the existing password and sessions valid until redemption.
    return await issue_invitation(session, user, request, purpose="reset")


@router.patch("/{user_id}", response_model=ManagedUser)
async def update_user(user_id: str, payload: UserUpdate, actor: Administrator, session: Session):
    await lock_administration(session, actor)
    user = await session.scalar(select(User).where(User.id == user_id).with_for_update())
    if user is None:
        raise HTTPException(404, "User not found")
    role = payload.role if payload.role is not None else user.role
    active = payload.is_active if payload.is_active is not None else user.is_active
    if user.id == actor.user.id and (role != "admin" or not active):
        raise HTTPException(409, "Cannot demote or disable yourself")
    if (
        user.role == "admin"
        and user.is_active
        and user.password_hash
        and (role != "admin" or not active)
    ):
        remaining = await session.scalar(
            select(func.count())
            .select_from(User)
            .where(User.role == "admin", User.is_active.is_(True), User.password_hash.is_not(None))
        )
        if remaining <= 1:
            raise HTTPException(409, "Cannot remove the last administrator")
    changed = user.role != role or user.is_active != active
    user.role, user.is_active = role, active
    if changed:
        await session.execute(delete(UserSession).where(UserSession.user_id == user.id))
    if not active:
        await session.execute(
            delete(PasswordInvitation).where(PasswordInvitation.user_id == user.id)
        )
    expires = await session.scalar(
        select(PasswordInvitation.expires_at).where(PasswordInvitation.user_id == user.id)
    )
    await session.commit()
    return managed_user(user, expires)


async def public_invitation_request(
    request: Request,
    x_requested_with: Annotated[str | None, Header()] = None,
) -> None:
    check_origin(request)
    if x_requested_with != "leadflow":
        raise HTTPException(403, "Browser request header required")


async def find_invitation(session: AsyncSession, token: str):
    row = (
        await session.execute(
            select(PasswordInvitation, User)
            .join(User, User.id == PasswordInvitation.user_id)
            .where(
                PasswordInvitation.token_hash == token_hash(token),
                PasswordInvitation.expires_at > datetime.now(UTC),
                User.is_active.is_(True),
                or_(
                    and_(PasswordInvitation.purpose == "setup", User.password_hash.is_(None)),
                    and_(PasswordInvitation.purpose == "reset", User.password_hash.is_not(None)),
                ),
            )
        )
    ).first()
    if row is None:
        raise HTTPException(400, "Invitation unavailable")
    return row


@invitation_router.post(
    "/check", response_model=InvitationInfo, dependencies=[Depends(public_invitation_request)]
)
async def check_invitation(payload: InvitationToken, session: Session, request: Request):
    await limit_login(
        session,
        f"invitation:{token_hash(payload.token)}",
        request.client.host if request.client else "unknown",
    )
    invitation, user = await find_invitation(session, payload.token)
    return InvitationInfo(username=user.username, expires_at=utc_date(invitation.expires_at))


@invitation_router.post(
    "/accept", status_code=204, dependencies=[Depends(public_invitation_request)]
)
async def accept_invitation(payload: PasswordSetup, session: Session, request: Request):
    await limit_login(
        session,
        f"invitation:{token_hash(payload.token)}",
        request.client.host if request.client else "unknown",
    )
    invitation, user = await find_invitation(session, payload.token)
    user_id = user.id
    purpose = invitation.purpose
    await session.rollback()
    async with request.app.state.password_slots:
        encoded = await asyncio.to_thread(hash_password, payload.password)
    user = await session.scalar(select(User).where(User.id == user_id).with_for_update())
    if (
        user is None
        or not user.is_active
        or (purpose == "setup" and user.password_hash is not None)
        or (purpose == "reset" and user.password_hash is None)
    ):
        raise HTTPException(400, "Invitation unavailable")
    claimed = await session.execute(
        delete(PasswordInvitation).where(
            PasswordInvitation.token_hash == token_hash(payload.token),
            PasswordInvitation.user_id == user.id,
            PasswordInvitation.purpose == purpose,
            PasswordInvitation.expires_at > datetime.now(UTC),
        )
    )
    if claimed.rowcount != 1:
        raise HTTPException(400, "Invitation unavailable")
    user.password_hash = encoded
    await session.execute(delete(UserSession).where(UserSession.user_id == user.id))
    await session.execute(
        delete(LoginLimit).where(LoginLimit.key == token_hash(f"user:{user.username}"))
    )
    await session.commit()
