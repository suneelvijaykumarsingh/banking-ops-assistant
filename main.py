from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI()

ERROR_CODES = {
    "E001": "Account is frozen - check freeze code on account master",
    "E002": "Insufficient balance - verify available vs ledger balance",
    "E003": "Transaction limit exceeded - check daily transaction limits",
    "E004": "Invalid account number - ensure the account number is correct",
    "E005": "Unauthorized access - verify user credentials and permissions",
    "E006": "Duplicate transaction - check for repeated transaction requests",
}

class ErrorCode(BaseModel):
    code: str
    hint: str

@app.get("/health")
def health():
    return {"status": "up"}

@app.get("/error/{code}")
def lookup(code: str):
    if code not in ERROR_CODES:
        raise HTTPException(status_code=404, detail="Error code not found")
    return {"code": code, "hint": ERROR_CODES[code]}

@app.post("/error")
def add_code(item: ErrorCode):
    if item.code in ERROR_CODES:
        raise HTTPException(status_code=409, detail="Code already exists")
    ERROR_CODES[item.code] = item.hint
    return {"message": "Added", "code": item.code}