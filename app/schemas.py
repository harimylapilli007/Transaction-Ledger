from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated
from uuid import UUID

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

CENT = Decimal("0.01")

# Positive, at most 2 decimal places, fits NUMERIC(18,2). Normalized to 2 places.
Amount = Annotated[
    Decimal,
    Field(gt=0, max_digits=18, decimal_places=2, examples=["50.00"]),
    AfterValidator(lambda value: value.quantize(CENT)),
]
ReferenceId = Annotated[str, Field(min_length=1, max_length=128, examples=["order-1001"])]
Description = Annotated[str, Field(max_length=500)]


class TransactionType(StrEnum):
    CREDIT = "credit"
    DEBIT = "debit"


class RequestModel(BaseModel):
    # Unknown fields (e.g. "balance" on account creation) are rejected, not ignored.
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class AccountCreate(RequestModel):
    name: str = Field(min_length=1, max_length=200, examples=["Alice"])


class TransactionCreate(RequestModel):
    type: TransactionType
    amount: Amount
    description: Description = ""
    reference_id: ReferenceId


class TransferCreate(RequestModel):
    source_account_id: UUID
    destination_account_id: UUID
    amount: Amount
    description: Description = ""
    reference_id: ReferenceId


class ResponseModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class AccountResponse(ResponseModel):
    id: UUID
    name: str
    balance: Decimal
    created_at: datetime


class BalanceResponse(ResponseModel):
    account_id: UUID
    balance: Decimal


class TransactionResponse(ResponseModel):
    id: UUID
    account_id: UUID
    type: TransactionType
    amount: Decimal
    description: str
    reference_id: str
    transfer_id: UUID | None
    created_at: datetime


class Pagination(BaseModel):
    page: int
    limit: int
    total_records: int
    total_pages: int


class TransactionPage(BaseModel):
    data: list[TransactionResponse]
    pagination: Pagination


class TransferResponse(ResponseModel):
    id: UUID
    source_account_id: UUID
    destination_account_id: UUID
    amount: Decimal
    description: str
    reference_id: str
    created_at: datetime
    debit_transaction: TransactionResponse
    credit_transaction: TransactionResponse
