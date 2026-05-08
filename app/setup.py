import argparse
import os
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from alembic.script import ScriptDirectory
from app.src.enums import (
    CompanyStatus,
    GenderType,
    OperatorType,
    AccountStatus,
    VendorType,
    BusinessStatus,
)

from app.src import buckets, minio
from app.src.db import (
    CompanyWallet,
    ORMbase,
    Executive,
    ExecutiveRole,
    ExecutiveRoleMap,
    Wallet,
    get_db_url,
    engine,
    SessionLocal,
    Company,
    Operator,
    OperatorRole,
    OperatorRoleMap,
    Business,
    Vendor,
    VendorRole,
    VendorRoleMap,
)


def _alembic_cfg() -> Config:
    current_dir = os.path.dirname(__file__)
    alembic_ini = os.path.join(current_dir, "alembic.ini")
    alembic_cfg = Config(alembic_ini)
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
            "vehicle": {"create": True, "update": True, "delete": True},
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
                "duty": {"update": True},
                "assignment": {"create": True, "update": True, "delete": True},
                "statement": {"create": True},
            },
            "schedule": {"create": True, "update": True, "delete": True},
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
            "vehicle": {"create": False, "update": False, "delete": False},
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
                "duty": {"update": False},
                "assignment": {"create": False, "update": False, "delete": False},
                "statement": {"create": False},
            },
            "schedule": {"create": False, "update": False, "delete": False},
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
        name="Nixbug Softwares OPC Pvt Ltd",
        status=CompanyStatus.VERIFIED,
        address="Edava, Thiruvananthapuram, Kerala 695311",
        location="POINT(76.68899711264336 8.761725176790257)",
    )
    session.add(company)
    session.flush()
    wallet = Wallet(balance=0.0, name=company.name)
    session.add(wallet)
    session.flush()
    company_wallet_map = CompanyWallet(company_id=company.id, wallet_id=wallet.id)
    session.add(company_wallet_map)
    session.flush()

    operator = Operator(
        company_id=company.id,
        username="admin",
        password="password",
        gender=GenderType.OTHER,
        type=OperatorType.ADMIN,
        full_name="Admin",
        status=AccountStatus.ACTIVE,
        phone_number="+91-9496801157",
        email_id="contact@nixbug.com",
    )
    session.add(operator)
    session.flush()

    admin_permissions = {
        "company": {
            "update": True,
            "vehicle": {"create": True, "update": True, "delete": True},
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
                "duty": {"update": True},
                "assignment": {"create": True, "update": True, "delete": True},
                "ticket": {"create": True},
                "statement": {"create": True},
            },
            "schedule": {"create": True, "update": True, "delete": True},
        },
    }

    guest = Operator(
        company_id=company.id,
        username="guest",
        password="password",
        gender=GenderType.OTHER,
        type=OperatorType.NORMAL,
        full_name="Guest",
        status=AccountStatus.ACTIVE,
        phone_number="+91-9496801111",
        email_id="contact@nixbug.com",
    )
    session.add(guest)
    session.flush()

    guest_permissions = {
        "company": {
            "update": False,
            "vehicle": {"create": False, "update": False, "delete": False},
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
                "duty": {"update": False},
                "assignment": {"create": False, "update": False, "delete": False},
                "ticket": {"create": False},
                "statement": {"create": False},
            },
            "schedule": {"create": False, "update": False, "delete": False},
        },
    }

    admin_role = OperatorRole(
        company_id=company.id, name="Admin", permissions=admin_permissions
    )
    guest_role = OperatorRole(
        company_id=company.id, name="Guest", permissions=guest_permissions
    )

    session.add_all([admin_role, guest_role])
    session.flush()

    admin_role_map = OperatorRoleMap(
        company_id=company.id, role_id=admin_role.id, operator_id=operator.id
    )
    session.add_all([admin_role_map])

    guest_role_map = OperatorRoleMap(
        company_id=company.id, role_id=guest_role.id, operator_id=guest.id
    )
    session.add_all([guest_role_map])

    business = Business(
        name="Nixbug Softwares OPC Pvt Ltd",
        status=BusinessStatus.ACTIVE,
        address="Edava, Thiruvananthapuram, Kerala 695311",
        location="POINT(76.69065175172149 8.761272913919761)",
    )
    session.add(business)
    session.flush()

    admin_vendor = Vendor(
        business_id=business.id,
        username="admin",
        password="password",
        gender=GenderType.OTHER,
        type=VendorType.ADMIN,
        full_name="Admin",
        status=AccountStatus.ACTIVE,
        phone_number="+91-9496801157",
        email_id="contact@nixbug.com",
    )
    session.add(admin_vendor)
    session.flush()

    guest_vendor = Vendor(
        business_id=business.id,
        username="guest",
        password="password",
        gender=GenderType.OTHER,
        type=VendorType.NORMAL,
        full_name="Guest",
        status=AccountStatus.ACTIVE,
        phone_number="+91-9496801111",
        email_id="contacthr@nixbug.com",
    )
    session.add(guest_vendor)
    session.flush()

    admin_permissions = {
        "business": {
            "update": True,
            "vendor": {
                "create": True,
                "update": True,
                "delete": True,
                "role": {
                    "create": True,
                    "update": True,
                    "delete": True,
                },
                "token": {
                    "fetch": True,
                    "delete": True,
                },
            },
        }
    }

    guest_permissions = {
        "business": {
            "update": False,
            "vendor": {
                "create": False,
                "update": False,
                "delete": False,
                "role": {
                    "create": False,
                    "update": False,
                    "delete": False,
                },
                "token": {
                    "fetch": False,
                    "delete": False,
                },
            },
        }
    }

    admin_role = VendorRole(
        business_id=business.id, name="Admin", permissions=admin_permissions
    )
    guest_role = VendorRole(
        business_id=business.id, name="Guest", permissions=guest_permissions
    )

    session.add_all([admin_role, guest_role])
    session.flush()

    admin_role_map = VendorRoleMap(
        business_id=business.id, role_id=admin_role.id, vendor_id=admin_vendor.id
    )
    session.add_all([admin_role_map])

    guest_role_map = VendorRoleMap(
        business_id=business.id, role_id=guest_role.id, vendor_id=guest_vendor.id
    )
    session.add_all([guest_role_map])

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
    subparsers = parser.add_subparsers(
        dest="group", required=True, help="Command group"
    )

    tables_parser = subparsers.add_parser("tables", help="Table and migration commands")
    tables_subparsers = tables_parser.add_subparsers(
        dest="command", required=True, help="Table command"
    )

    tables_subparsers.add_parser("create", help="Create all tables")
    tables_subparsers.add_parser("delete", help="Delete all tables")
    tables_subparsers.add_parser(
        "init", help="Initialize the database with default data"
    )
    tables_subparsers.add_parser("reset", help="Reset the database schema")
    tables_subparsers.add_parser("migrate", help="Run migrations to head")

    # Downgrade with optional steps
    downgrade_sp = tables_subparsers.add_parser(
        "downgrade", help="Downgrade the schema"
    )
    downgrade_sp.add_argument(
        "steps",
        nargs="?",
        default="-1",
        help="Number of steps to downgrade (default: -1)",
    )

    # Revise with optional message
    revise_sp = tables_subparsers.add_parser(
        "revise", help="Create a new migration revision"
    )
    revise_sp.add_argument(
        "message", nargs="?", default="auto revise", help="Revision message"
    )

    buckets_parser = subparsers.add_parser("buckets", help="Storage bucket commands")
    buckets_subparsers = buckets_parser.add_subparsers(
        dest="command", required=True, help="Bucket command"
    )

    buckets_subparsers.add_parser("create", help="Create storage buckets")
    buckets_subparsers.add_parser("delete", help="Delete storage buckets")

    args = parser.parse_args()

    # Dispatch based on group and command
    if args.group == "tables":
        if args.command == "create":
            create_tables()
        elif args.command == "delete":
            delete_tables()
        elif args.command == "init":
            initialize()
        elif args.command == "reset":
            reset_db()
        elif args.command == "migrate":
            migrate()
        elif args.command == "downgrade":
            downgrade(args.steps)
        elif args.command == "revise":
            revise(args.message)
    elif args.group == "buckets":
        if args.command == "create":
            create_buckets()
        elif args.command == "delete":
            delete_buckets()


if __name__ == "__main__":
    main()
