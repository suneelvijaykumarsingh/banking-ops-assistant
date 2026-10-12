import json
from pathlib import Path
import psycopg
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI()
DB = "dbname=bankops"
SEED_FILE = Path("error_codes.json")

def get_conn():
    return psycopg.connect(DB)

def init_db():
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS error_codes (
                code       VARCHAR(20) PRIMARY KEY,
                hint       TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        count = conn.execute("SELECT COUNT(*) FROM error_codes").fetchone()[0]
        if count == 0 and SEED_FILE.exists():
            codes = json.loads(SEED_FILE.read_text())
            for code, hint in codes.items():
                conn.execute(
                    "INSERT INTO error_codes (code, hint) VALUES (%s, %s)",
                    (code, hint),
                )

init_db()

class ErrorCode(BaseModel):
    code: str
    hint: str

@app.get("/health")
def health():
    with get_conn() as conn:
        total = conn.execute("SELECT COUNT(*) FROM error_codes").fetchone()[0]
    return {"status": "up", "database": "connected", "total_codes": total}

@app.get("/errors")
def list_codes():
    with get_conn() as conn:
        rows = conn.execute("SELECT code, hint FROM error_codes ORDER BY code").fetchall()
    return {code: hint for code, hint in rows}

@app.get("/error/{code}")
def lookup(code: str):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT hint FROM error_codes WHERE code = %s", (code,)
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Error code not found")
    return {"code": code, "hint": row[0]}

@app.get("/search")
def search(q: str):
    pattern = f"%{q}%"
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT code, hint FROM error_codes WHERE code ILIKE %s OR hint ILIKE %s ORDER BY code",
            (pattern, pattern),
        ).fetchall()
    return {"query": q, "matches": [{"code": c, "hint": h} for c, h in rows]}

@app.post("/error")
def add_code(item: ErrorCode):
    try:
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO error_codes (code, hint) VALUES (%s, %s)",
                (item.code, item.hint),
            )
    except psycopg.errors.UniqueViolation:
        raise HTTPException(status_code=409, detail="Code already exists")
    return {"message": "Added", "code": item.code}

@app.delete("/error/{code}")
def delete_code(code: str):
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM error_codes WHERE code = %s", (code,))
    if cur.rowcount == 0:
        raise HTTPException(status_code=404, detail="Error code not found")
    return {"message": "Deleted", "code": code}