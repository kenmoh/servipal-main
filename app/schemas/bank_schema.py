from pydantic import BaseModel


class AccountDetails(BaseModel):
    account_number: str
    account_bank: str


class AccountDetailResponse(BaseModel):
    account_number: str
    account_name: str


class BankSchema(BaseModel):
    id: int
    code: str
    name: str
