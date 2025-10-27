import argparse
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from alembic.script import ScriptDirectory

from app.src import argon2, buckets, minio
from app.src.db import (
    ORMbase,
    Executive,
    ExecutiveRole,
    ExecutiveRoleMap,
    get_db_url,
    engine,
    SessionLocal,
)


def _alembic_cfg() -> Config:
    alembic_cfg = Config("app/alembic.ini")
    alembic_cfg.set_main_option("sqlalchemy.url", get_db_url())
    return alembic_cfg


def revise(message="auto upgrade"):
    """
    Compare current DB with models, create a revision if needed.
    Works for first-time migrations as well.
    """
    alembic_cfg = _alembic_cfg()
    script = ScriptDirectory.from_config(alembic_cfg)

    # If no revisions exist, create initial revision
    if not list(script.walk_revisions(base="base", head="heads")):
        print("* No revisions found, creating initial revision")
        command.revision(alembic_cfg, message="initial schema", autogenerate=True)
        return

    # Make sure DB is at head
    command.upgrade(alembic_cfg, "head")

    # Now generate a new revision based on model differences
    print("* Generating new revision for model changes...")
    command.revision(alembic_cfg, message=message, autogenerate=True)


def migrate():
    """Run migrations up to head. Only applies existing revisions."""
    alembic_cfg = _alembic_cfg()
    command.upgrade(alembic_cfg, "head")
    print("* Database migrated to head")


def reset_db():
    """Drop everything and recreate schema from migrations."""
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE;"))
        conn.execute(text("CREATE SCHEMA public;"))
    print("* Database schema reset")


def downgrade(step):
    command.downgrade(_alembic_cfg(), step)
    print(f"* Database downgraded to {step}")


def create_buckets():
    for bucket in buckets.ALL:
        minio.create_bucket(bucket)
    print("* All buckets created")


def delete_buckets():
    for bucket in buckets.ALL:
        minio.delete_bucket(bucket)
    print("* All buckets deleted")


def create_tables():
    session = SessionLocal()
    ORMbase.metadata.create_all(engine)
    session.commit()
    print("* All tables created")
    session.close()


def delete_tables():
    session = SessionLocal()
    ORMbase.metadata.drop_all(engine)
    session.commit()
    print("* All tables deleted")
    session.close()


def initialize():
    """Initialize the database with default users with default permissions."""
    session = SessionLocal()

    password = argon2.make_password("password")
    admin = Executive(
        username="admin",
        password=password,
        full_name="Entebus admin",
        designation="Administrator",
    )
    guest = Executive(
        username="guest",
        password=password,
        full_name="Entebus guest",
        designation="Guest",
    )

    session.add_all([admin, guest])
    session.flush()

    admin_permissions = {
        "landmark": {
            "fetch": True,
            "create": True,
            "update": True,
            "delete": True,
            "bus_stop": {"fetch": True, "create": True, "update": True, "delete": True},
        },
        "fare": {"fetch": True, "create": True, "update": True, "delete": True},
        "executive": {
            "fetch": True,
            "create": True,
            "update": True,
            "delete": True,
            "role": {"fetch": True, "create": True, "update": True, "delete": True},
            "token": {"fetch": True},
        },
        "business": {
            "fetch": True,
            "create": True,
            "update": True,
            "delete": True,
            "vendor": {
                "fetch": True,
                "create": True,
                "update": True,
                "delete": True,
                "role": {"fetch": True, "create": True, "update": True, "delete": True},
                "token": {"fetch": True},
            },
        },
        "company": {
            "fetch": True,
            "create": True,
            "update": True,
            "delete": True,
            "bus": {"fetch": True, "create": True, "update": True, "delete": True},
            "fare": {"fetch": True, "create": True, "update": True, "delete": True},
            "route": {"fetch": True, "create": True, "update": True, "delete": True},
            "operator": {
                "fetch": True,
                "create": True,
                "update": True,
                "delete": True,
                "role": {"fetch": True, "create": True, "update": True, "delete": True},
                "token": {"fetch": True},
            },
            "service": {
                "fetch": True,
                "create": True,
                "update": True,
                "delete": True,
                "duty": {"fetch": True, "create": True, "update": True, "delete": True},
            },
        },
    }

    guest_permissions = {
        "landmark": {"fetch": True, "bus_stop": {"fetch": True}},
        "fare": {"fetch": True},
        "executive": {"fetch": True, "role": {"fetch": True}, "token": {"fetch": True}},
        "business": {
            "fetch": True,
            "vendor": {
                "fetch": True,
                "role": {"fetch": True},
                "token": {"fetch": True},
            },
        },
        "company": {
            "fetch": True,
            "bus": {"fetch": True},
            "fare": {"fetch": True},
            "route": {"fetch": True},
            "operator": {
                "fetch": True,
                "role": {"fetch": True},
                "token": {"fetch": True},
            },
            "service": {"fetch": True, "duty": {"fetch": True}},
        },
    }

    admin_role = ExecutiveRole(name="Admin", permissions=admin_permissions)
    guest_role = ExecutiveRole(name="Guest", permissions=guest_permissions)

    session.add_all([admin_role, guest_role])
    session.flush()

    admin_role_map = ExecutiveRoleMap(role_id=admin_role.id, executive_id=admin.id)
    guest_role_map = ExecutiveRoleMap(role_id=guest_role.id, executive_id=guest.id)
    session.add_all([admin_role_map, guest_role_map])

    session.commit()
    print("* Initialization completed")
    session.close()


# ---- Argparse setup ----
def main():
    parser = argparse.ArgumentParser(
        description="Database migration and management tool"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # downgrade
    p_downgrade = subparsers.add_parser("downgrade", help="Downgrade the schema")
    p_downgrade.add_argument(
        "steps",
        nargs="?",
        default="-1",
        help="Number of steps to downgrade (default: -1)",
    )

    # revise
    p_revise = subparsers.add_parser("revise", help="Create a new migration revision")
    p_revise.add_argument(
        "message", nargs="?", default="auto revise", help="Revision message"
    )

    subparsers.add_parser("reset_db", help="Reset the database")
    subparsers.add_parser("migrate", help="Run migrations")
    subparsers.add_parser("create_tables", help="Create all tables")
    subparsers.add_parser("delete_tables", help="Delete all tables")
    subparsers.add_parser("create_buckets", help="Create storage buckets")
    subparsers.add_parser("delete_buckets", help="Delete storage buckets")
    subparsers.add_parser("initialize", help="Initialize the server environment")
    args = parser.parse_args()

    if args.command == "downgrade":
        downgrade(args.steps)
    elif args.command == "reset_db":
        reset_db()
    elif args.command == "migrate":
        migrate()
    elif args.command == "revise":
        revise(args.message)
    elif args.command == "create_tables":
        create_tables()
    elif args.command == "delete_tables":
        delete_tables()
    elif args.command == "create_buckets":
        create_buckets()
    elif args.command == "delete_buckets":
        delete_buckets()
    if args.command == "initialize":
        initialize()


if __name__ == "__main__":
    main()
