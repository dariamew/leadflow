import asyncio
import hashlib
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, Security
from fastapi.security import APIKeyCookie
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import case, delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import LoginLimit, User, UserSession

COOKIE_NAME = "leadflow_session"
Session = Annotated[AsyncSession, Depends(get_session)]
session_cookie = APIKeyCookie(name=COOKIE_NAME, auto_error=False)
router = APIRouter(prefix="/api/v1/auth", tags=["authentication"])


def normalize_username(value: str) -> str:
    value = value.strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{2,63}", value):
        raise ValueError(
            "Login must contain 3–64 Latin letters, digits, dots, hyphens or underscores"
        )
    return value


def hash_password(password: str) -> str:
    if not 12 <= len(password) <= 128:
        raise ValueError("Password must contain 12–128 characters")
    salt = secrets.token_hex(16)
    digest = hashlib.scrypt(
        password.encode(),
        salt=bytes.fromhex(salt),
        n=2**17,
        r=8,
        p=1,
        maxmem=256 * 1024 * 1024,
    )
    return f"scrypt$131072$8$1${salt}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, n, r, p, salt, expected = encoded.split("$")
        if (algorithm, n, r, p) != ("scrypt", "131072", "8", "1"):
            return False
        digest = hashlib.scrypt(
            password.encode(),
            salt=bytes.fromhex(salt),
            n=int(n),
            r=int(r),
            p=int(p),
            maxmem=256 * 1024 * 1024,
        )
        return secrets.compare_digest(digest.hex(), expected)
    except (ValueError, TypeError):
        return False


# A valid, fixed dummy hash keeps unknown-user and wrong-password work equivalent.
DUMMY_HASH = "scrypt$131072$8$1$" + "00" * 16 + "$" + "00" * 64


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def check_origin(request: Request) -> None:
    expected = request.app.state.settings.auth_public_origin or str(request.base_url).rstrip("/")
    origin = request.headers.get("origin")
    if origin is not None and origin != expected:
        raise HTTPException(403, "Untrusted origin")


@dataclass
class AuthContext:
    user: User
    session: UserSession


async def authenticate(
    request: Request,
    session: Session,
    cookie: Annotated[str | None, Security(session_cookie)],
    x_csrf_token: Annotated[str | None, Header()] = None,
) -> AuthContext:
    if not cookie or len(cookie) > 128:
        raise HTTPException(401, "Authentication required")
    row = (
        await session.execute(
            select(UserSession, User)
            .join(User, User.id == UserSession.user_id)
            .where(
                UserSession.token_hash == token_hash(cookie),
                UserSession.expires_at > datetime.now(UTC),
                User.is_active.is_(True),
            )
        )
    ).first()
    if row is None:
        raise HTTPException(401, "Authentication required")
    user_session, user = row
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        check_origin(request)
        csrf = x_csrf_token or ""
        if not secrets.compare_digest(csrf.encode(), user_session.csrf_token.encode()):
            raise HTTPException(403, "Invalid CSRF token")
    return AuthContext(user, user_session)


CurrentUser = Annotated[AuthContext, Depends(authenticate)]


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class UserRead(BaseModel):
    id: str
    username: str
    role: str


class AuthRead(BaseModel):
    user: UserRead
    csrf_token: str


def auth_read(context: AuthContext) -> AuthRead:
    return AuthRead(
        user=UserRead(id=context.user.id, username=context.user.username, role=context.user.role),
        csrf_token=context.session.csrf_token,
    )


async def limit_login(session: AsyncSession, username: str, address: str) -> None:
    now = datetime.now(UTC)
    resets = now + timedelta(minutes=10)
    await session.execute(delete(LoginLimit).where(LoginLimit.resets_at <= now))
    insert = sqlite_insert if session.bind.dialect.name == "sqlite" else pg_insert
    exceeded = False
    # Atomic upserts keep limits shared by processes and across restarts.
    for value, limit in [(f"user:{username}", 10), (f"ip:{address}", 30)]:
        statement = insert(LoginLimit).values(key=token_hash(value), attempts=1, resets_at=resets)
        statement = statement.on_conflict_do_update(
            index_elements=[LoginLimit.key],
            set_={
                "attempts": case((LoginLimit.resets_at <= now, 1), else_=LoginLimit.attempts + 1),
                "resets_at": case(
                    (LoginLimit.resets_at <= now, resets), else_=LoginLimit.resets_at
                ),
            },
        ).returning(LoginLimit.attempts)
        exceeded |= (await session.scalar(statement)) > limit
    await session.commit()
    if exceeded:
        raise HTTPException(429, "Too many login attempts", headers={"Retry-After": "600"})


@router.post("/login", response_model=AuthRead)
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    session: Session,
    x_requested_with: Annotated[str | None, Header(description="Set to leadflow")] = None,
):
    check_origin(request)
    # Custom header + JSON prevents login CSRF even before a session exists. No CORS is enabled.
    if x_requested_with != "leadflow":
        raise HTTPException(403, "Browser request header required")
    username = payload.username.strip().lower()
    address = request.client.host if request.client else "unknown"
    await limit_login(session, username, address)
    user = await session.scalar(select(User).where(User.username == username))
    async with request.app.state.password_slots:
        valid = await asyncio.to_thread(
            verify_password,
            payload.password,
            user.password_hash if user and user.password_hash else DUMMY_HASH,
        )
    if not valid or user is None or not user.is_active:
        raise HTTPException(401, "Invalid credentials")
    # Re-read after hashing: a concurrent password reset/deactivation must win.
    original_hash = user.password_hash
    user = await session.scalar(
        select(User)
        .where(User.id == user.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if user is None or not user.is_active or user.password_hash != original_hash:
        raise HTTPException(401, "Invalid credentials")
    now = datetime.now(UTC)
    await session.execute(delete(UserSession).where(UserSession.expires_at <= now))
    previous = request.cookies.get(COOKIE_NAME)
    if previous:
        await session.execute(
            delete(UserSession).where(UserSession.token_hash == token_hash(previous))
        )
    token = secrets.token_urlsafe(32)
    ttl = request.app.state.settings.auth_session_hours * 3600
    user_session = UserSession(
        token_hash=token_hash(token),
        user_id=user.id,
        csrf_token=secrets.token_hex(32),
        expires_at=now + timedelta(seconds=ttl),
    )
    session.add(user_session)
    await session.commit()
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=ttl,
        httponly=True,
        secure=request.app.state.settings.auth_cookie_secure,
        samesite="strict",
        path="/",
    )
    response.headers["Cache-Control"] = "no-store"
    return auth_read(AuthContext(user, user_session))


@router.get("/me", response_model=AuthRead)
async def me(context: CurrentUser):
    return auth_read(context)


@router.post("/logout", status_code=204)
async def logout(context: CurrentUser, session: Session, request: Request):
    await session.delete(context.session)
    await session.commit()
    response = Response(status_code=204)
    response.delete_cookie(
        COOKIE_NAME,
        httponly=True,
        secure=request.app.state.settings.auth_cookie_secure,
        samesite="strict",
        path="/",
    )
    return response
