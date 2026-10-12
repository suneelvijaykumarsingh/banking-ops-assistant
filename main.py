import json
from pathlib import Path
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI()
DATA_FILE = Path("error_codes.json")

DEFAULT_CODES = {
    "E001": "Account is frozen - check freeze code on account master",
    "E002": "Insufficient balance - verify available vs ledger balance",
    "E003": "Transaction limit exceeded - check daily transaction limits",
    "E004": "Invalid account number - ensure the account number is correct",
    "E005": "Unauthorized access - verify user credentials and permissions",
    "E006": "Duplicate transaction - check for repeated transaction requests",
}

# ---------- Storage functions ----------
def load_codes():
    try:
        with open(DATA_FILE) as f:
            return json.load(f)
    except FileNotFoundError:
        save_codes(DEFAULT_CODES)
        return dict(DEFAULT_CODES)

def save_codes(codes):
    with open(DATA_FILE, "w") as f:
        json.dump(codes, f, indent=2)

ERROR_CODES = load_codes()

# ---------- API ----------
class ErrorCode(BaseModel):
    code: str
    hint: str

@app.get("/health")
def health():
    return {"status": "up", "total_codes": len(ERROR_CODES)}

@app.get("/errors")
def list_codes():
    return ERROR_CODES

@app.get("/error/{code}")
def lookup(code: str):
    if code not in ERROR_CODES:
        raise HTTPException(status_code=404, detail="Error code not found")
    return {"code": code, "hint": ERROR_CODES[code]}

@app.get("/search")
def search(q: str):
    results = []
    for code, hint in ERROR_CODES.items():
        if q.lower() in hint.lower():
            results.append({"code": code, "hint": hint})
    return {"query": q, "matches": results}

@app.post("/error")
def add_code(item: ErrorCode):
    if item.code in ERROR_CODES:
        raise HTTPException(status_code=409, detail="Code already exists")
    ERROR_CODES[item.code] = item.hint
    save_codes(ERROR_CODES)
    return {"message": "Added", "code": item.code}