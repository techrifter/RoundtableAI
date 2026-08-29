from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    groq_api_key: str
    groq_model: str

    agora_app_id: str
    agora_app_certificate: str
    # RESTful credentials for the Conversational AI Engine (Basic auth).
    agora_customer_id: str
    agora_customer_secret: str

    # Public HTTPS origin of this backend. Agora calls back to it for the custom
    # LLM, so it must be reachable from the internet.
    public_base_url: str


settings = Settings()
