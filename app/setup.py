import argparse
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from alembic.script import ScriptDirectory
from app.src.enums import CompanyStatus

from app.src import buckets, minio
from app.src.db import (
    ORMbase,
    Executive,
    ExecutiveRole,
    ExecutiveRoleMap,
    get_db_url,
    engine,
    SessionLocal,
    Company,
    Operator
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

    admin = Executive(
        username="admin",
        password="password",
        full_name="Entebus admin",
        designation="Administrator",
    )
    guest = Executive(
        username="guest",
        password="password",
        full_name="Entebus guest",
        designation="Guest",
    )

    session.add_all([admin, guest])
    session.flush()

    admin_permissions = {
        "landmark": {
            "create": True,
            "update": True,
            "delete": True,
            "bus_stop": {"create": True, "update": True, "delete": True},
        },
        "fare": {"create": True, "update": True, "delete": True},
        "executive": {
            "create": True,
            "update": True,
            "delete": True,
            "role": {"create": True, "update": True, "delete": True},
            "token": {"fetch": True, "delete": True},
        },
        "business": {
            "create": True,
            "update": True,
            "delete": True,
            "vendor": {
                "create": True,
                "update": True,
                "delete": True,
                "role": {"create": True, "update": True, "delete": True},
                "token": {"fetch": True, "delete": True},
            },
        },
        "company": {
            "create": True,
            "update": True,
            "delete": True,
            "bus": {"create": True, "update": True, "delete": True},
            "fare": {"create": True, "update": True, "delete": True},
            "route": {"create": True, "update": True, "delete": True},
            "operator": {
                "create": True,
                "update": True,
                "delete": True,
                "role": {"create": True, "update": True, "delete": True},
                "token": {"fetch": True, "delete": True},
            },
            "service": {
                "create": True,
                "update": True,
                "delete": True,
                "duty": {"create": True, "update": True, "delete": True},
            },
        },
    }

    guest_permissions = {
        "landmark": {
            "create": False,
            "update": False,
            "delete": False,
            "bus_stop": {"create": False, "update": False, "delete": False},
        },
        "fare": {"create": False, "update": False, "delete": False},
        "executive": {
            "create": False,
            "update": False,
            "delete": False,
            "role": {"create": False, "update": False, "delete": False},
            "token": {"fetch": False, "delete": False},
        },
        "business": {
            "create": False,
            "update": False,
            "delete": False,
            "vendor": {
                "create": False,
                "update": False,
                "delete": False,
                "role": {"create": False, "update": False, "delete": False},
                "token": {"fetch": False, "delete": False},
            },
        },
        "company": {
            "create": False,
            "update": False,
            "delete": False,
            "bus": {"create": False, "update": False, "delete": False},
            "fare": {"create": False, "update": False, "delete": False},
            "route": {"create": False, "update": False, "delete": False},
            "operator": {
                "create": False,
                "update": False,
                "delete": False,
                "role": {"create": False, "update": False, "delete": False},
                "token": {"fetch": False, "delete": False},
            },
            "service": {
                "create": False,
                "update": False,
                "delete": False,
                "duty": {"create": False, "update": False, "delete": False},
            },
        },
    }

    admin_role = ExecutiveRole(name="Admin", permissions=admin_permissions)
    guest_role = ExecutiveRole(name="Guest", permissions=guest_permissions)

    session.add_all([admin_role, guest_role])
    session.flush()

    admin_role_map = ExecutiveRoleMap(role_id=admin_role.id, executive_id=admin.id)
    guest_role_map = ExecutiveRoleMap(role_id=guest_role.id, executive_id=guest.id)
    session.add_all([admin_role_map, guest_role_map])

    company = Company(
        name="Nixbug company",
        status=CompanyStatus.VERIFIED,
        address="Edava, Thiruvananthapuram, Kerala 695311",
        location="POINT(76.68899711264336 8.761725176790257)"
    )
    session.add(company)
    session.flush()

    operator = Operator(
        company_id=company.id,
        username="operator1",
        password="password",  # Will be hashed by event listener
        gender=0,
        type=0,
        full_name="Operator One",
        status=1,
        phone_number="+1-202-555-0143",
        email_id="operator1@nixbug.com"
    )
    session.add(operator)
    session.flush()


    session.commit()
    print("* Initialization completed")
    session.close()


# ---------------------------------------------------------------------------
## Setup main entry point
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Database migration and management tool"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Downgrade
    downgrade_sp = subparsers.add_parser("downgrade", help="Downgrade the schema")
    downgrade_sp.add_argument(
        "steps",
        nargs="?",
        default="-1",
        help="Number of steps to downgrade (default: -1)",
    )

    # Revise
    revise_sp = subparsers.add_parser("revise", help="Create a new migration revision")
    revise_sp.add_argument(
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
