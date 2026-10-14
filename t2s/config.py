import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()  # reads .env from the project root


def _require(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing environment variable {name}. Check your .env file.")
    return value


@dataclass(frozen=True)
class Settings:
    groq_api_key: str
    groq_model: str
    pg_host: str
    pg_port: int
    pg_db: str
    pg_user: str          # owner account: admin scripts only
    pg_password: str
    pg_ro_user: str       # read-only account: the app uses this one
    pg_ro_password: str
    pg_log_user: str      # query-log account: can only append to and read text2sql.query_log
    pg_log_password: str


def get_settings() -> Settings:
    return Settings(
        groq_api_key=_require("GROQ_API_KEY"),
        groq_model=os.getenv("GROQ_MODEL", "openai/gpt-oss-120b"),
        pg_host=_require("PG_HOST"),
        pg_port=int(_require("PG_PORT")),
        pg_db=_require("PG_DB"),
        pg_user=_require("PG_USER"),
        pg_password=_require("PG_PASSWORD"),
        pg_ro_user=os.getenv("PG_RO_USER", "text2sql_ro"),
        pg_ro_password=_require("PG_RO_PASSWORD"),
        pg_log_user=os.getenv("PG_LOG_USER", "text2sql_logger"),
        pg_log_password=_require("PG_LOG_PASSWORD"),
    )