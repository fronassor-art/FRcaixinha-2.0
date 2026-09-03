from pydantic import BaseModel, EmailStr, Field, field_validator

class RegisterIn(BaseModel):
    name: str = Field(min_length=2, max_length=150)
    email: EmailStr
    cpf: str = Field(min_length=11, max_length=14)
    phone: str | None = None
    password: str = Field(min_length=10, max_length=128)
    accept_terms: bool

class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(max_length=128)

class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    two_factor_required: bool = False

class PasswordChangeIn(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=10, max_length=128)

class PasswordResetRequestIn(BaseModel):
    email: EmailStr

class PasswordResetConfirmIn(BaseModel):
    token: str = Field(min_length=40, max_length=200)
    new_password: str = Field(min_length=10, max_length=128)

class TwoFactorVerifyIn(BaseModel):
    challenge_token: str = Field(min_length=20, max_length=2000)
    code: str = Field(min_length=6, max_length=16)
    trust_device: bool = False

class TwoFactorCodeIn(BaseModel):
    code: str = Field(min_length=6, max_length=16)
