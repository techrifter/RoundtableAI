from pydantic import BaseModel

class UserBYOKCredentials(BaseModel):
    uid: str
    encrypted_app_id: str
    encrypted_app_certificate: str
    encrypted_customer_id: str
    encrypted_customer_secret: str
    encrypted_groq_api_key: str = ""
    connection_status: str
    created_at_timestamp: float
    updated_at_timestamp: float
