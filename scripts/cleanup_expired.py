from app.db import SessionLocal, init_db
from app.utils import cleanup_expired_batches


def main() -> None:
    init_db()
    db = SessionLocal()
    try:
        deleted = cleanup_expired_batches(db)
        print(f"Deleted expired batches: {deleted}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
