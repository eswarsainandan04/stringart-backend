from pathlib import Path
from sqlalchemy import text
from database import engine


def init_db():
    sql_path = Path(__file__).parent / "database.sql"

    if not sql_path.exists():
        print(f"ERROR: Could not find {sql_path}")
        return

    sql_content = sql_path.read_text(encoding="utf-8").strip()

    if not sql_content:
        print("WARNING: database.sql is empty. Put your CREATE TABLE queries inside it first!")
        return

    print("Executing database.sql on PostgreSQL...")
    try:
        with engine.connect() as connection:
            connection.execute(text(sql_content))
            connection.commit()
            print("SUCCESS: Database initialized from database.sql!")
    except Exception as e:
        print(f"FAILED to execute database.sql: {e}")


if __name__ == "__main__":
    init_db()
