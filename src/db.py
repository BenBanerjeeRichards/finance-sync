from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import os


engine = create_engine(
    os.environ["PSQL_CONNECTION_STRING"],
    executemany_mode="values_plus_batch",
    executemany_batch_page_size=1000,
)
DBSession = sessionmaker(engine)


def get_db_session():
    db = DBSession()
    try:
        yield db
        db.commit()
    except:
        db.rollback()
        raise
    finally:
        db.close()
