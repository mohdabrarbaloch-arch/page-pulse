"""Create a demo account and seed the sample pages.

Run once after installing:

    python -m scripts.seed

It is safe to run twice: an existing account or page is reported and skipped
rather than duplicated.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import Base, SessionLocal, engine
from app.models import Page, User
from app.security import hash_password
from app.services.demo_data import available_demo_pages
from app.services.pages import PageAlreadyExistsError, create_page_from_demo

DEMO_EMAIL = "abraz@example.com"
DEMO_PASSWORD = "pagepulse-demo-2026"


def main() -> int:
    Base.metadata.create_all(bind=engine)

    with SessionLocal() as db:
        user = db.query(User).filter(User.email == DEMO_EMAIL).one_or_none()
        if user is None:
            user = User(
                email=DEMO_EMAIL,
                password_hash=hash_password(DEMO_PASSWORD),
                display_name="ABraz Baloch",
            )
            db.add(user)
            db.commit()
            db.refresh(user)
            print(f"created account  {DEMO_EMAIL}")
        else:
            print(f"account exists   {DEMO_EMAIL}")

        for demo in available_demo_pages():
            try:
                page = create_page_from_demo(db, user, demo["slug"], days=180)
            except PageAlreadyExistsError:
                print(f"page exists      {demo['name']}")
                continue
            except KeyError:
                print(f"unknown slug     {demo['slug']}")
                continue
            count = db.query(Page).filter(Page.id == page.id).one().posts.__len__()
            print(f"seeded page      {page.name} ({count} posts)")

    print()
    print("Sign in with:")
    print(f"  email     {DEMO_EMAIL}")
    print(f"  password  {DEMO_PASSWORD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
