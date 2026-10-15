"""Server-owned configuration; these fields are never MCP tool arguments."""

from pydantic import AnyHttpUrl, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DOMAIN_KG_", env_file=".env", extra="ignore")

    public_url: AnyHttpUrl = AnyHttpUrl("http://127.0.0.1:8000")
    oidc_issuer: AnyHttpUrl = AnyHttpUrl("http://127.0.0.1:18080/realms/domain-kg")
    mongo_uri: SecretStr = SecretStr(
        "mongodb://127.0.0.1:27027/?replicaSet=rs0&directConnection=true"
    )
    neo4j_uri: str = "bolt://127.0.0.1:27687"
    neo4j_user: str = "neo4j"
    neo4j_password: SecretStr = SecretStr("")
    qdrant_url: str = "http://127.0.0.1:26333"
    qdrant_api_key: SecretStr = SecretStr("")
    s3_endpoint: str = "http://127.0.0.1:29000"
    s3_access_key: str = "domain-kg-dev"
    s3_secret_key: SecretStr = SecretStr("")
