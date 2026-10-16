"""Manage local accounts: python -m app.users create|set-password USERNAME."""

import argparse
import asyncio
from getpass import getpass

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from app.auth import hash_password, normalize_username
from app.config import Settings
from app.db import make_database
from app.models import PasswordInvitation, User, UserSession


async def save_user(
    username: str, password: str, *, reset: bool = False, role: str = "manager"
) -> None:
    if role not in {"admin", "manager"}:
        raise ValueError("Неизвестная роль.")
    username = normalize_username(username)
    encoded = await asyncio.to_thread(hash_password, password)
    engine, sessions = make_database(Settings().database_url)
    try:
        async with sessions() as session:
            user = await session.scalar(
                select(User).where(User.username == username).with_for_update()
            )
            if reset:
                if user is None:
                    raise ValueError("Пользователь не найден.")
                user.password_hash = encoded
                await session.execute(delete(UserSession).where(UserSession.user_id == user.id))
                await session.execute(
                    delete(PasswordInvitation).where(PasswordInvitation.user_id == user.id)
                )
            else:
                if user is not None:
                    raise ValueError("Логин уже занят.")
                session.add(User(username=username, password_hash=encoded, role=role))
            try:
                await session.commit()
            except IntegrityError:
                raise ValueError("Логин уже занят.") from None
    finally:
        await engine.dispose()


def main():
    parser = argparse.ArgumentParser(description="Управление пользователями Leadflow")
    parser.add_argument("command", choices=["create", "set-password"])
    parser.add_argument("username")
    parser.add_argument(
        "--role", choices=["admin", "manager"], default="manager", help="Роль новой учётной записи"
    )
    args = parser.parse_args()
    try:
        username = normalize_username(args.username)
        password = getpass("Пароль (12–128 символов): ")
        if password != getpass("Повторите пароль: "):
            raise ValueError("Пароли не совпадают.")
        asyncio.run(
            save_user(username, password, reset=args.command == "set-password", role=args.role)
        )
    except ValueError as error:
        parser.exit(1, f"{error}\n")
    print(
        "Пароль обновлён; прежние сессии завершены."
        if args.command == "set-password"
        else "Пользователь создан."
    )


if __name__ == "__main__":
    main()
