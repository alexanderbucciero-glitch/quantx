from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="QUANTX_", extra="ignore")

    # SEC requires a descriptive User-Agent including contact details.
    # Set QUANTX_USER_AGENT="QuantX research your-email@example.com" in .env
    user_agent: str = "QuantX personal research (set QUANTX_USER_AGENT)"

    # Optional free keys. Empty = integration reported as "not configured".
    tiingo_api_key: str = ""      # free account at tiingo.com -> US/EU stock EOD history
    fred_api_key: str = ""        # optional; public CSV endpoint is used without it

    cors_origins: str = "http://localhost:3000"

    # Local SQLite by default; e.g. postgresql+psycopg://user:pass@localhost/quantx for Postgres/Timescale
    database_url: str = "sqlite:///./data/quantx.db"


settings = Settings()
