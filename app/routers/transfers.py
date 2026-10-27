from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app import services
from app.database import get_db
from app.schemas import TransferCreate, TransferResponse

router = APIRouter(prefix="/v1/transfers", tags=["transfers"])


@router.post("", status_code=status.HTTP_201_CREATED, response_model=TransferResponse)
def create_transfer(payload: TransferCreate, db: Session = Depends(get_db)):
    return services.transfer(db, payload)
