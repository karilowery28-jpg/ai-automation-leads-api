import os, re, sqlite3, secrets
import requests as http_requests
from datetime import datetime, timezone
from typing import List
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel, EmailStr, Field, validator
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address


DB_PATH = os.getenv('LEADS_DB_PATH', 'leads.db')
ADMIN_USER = os.getenv('ADMIN_USER', 'admin')
ADMIN_PASS = os.getenv('ADMIN_PASS', 'admin123')
API_HOST = os.getenv('API_HOST', '0.0.0.0')
API_PORT = int(os.getenv('API_PORT', '8001'))


def send_telegram_notification(message: str) -> None:
    token = os.getenv('TELEGRAM_BOT_TOKEN')
    chat_id = os.getenv('TELEGRAM_CHAT_ID')
    if not token or not chat_id:
        print('[Telegram] missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID')
        return
    try:
        resp = http_requests.post(
            f'https://api.telegram.org/bot{token}/sendMessage',
            json={'chat_id': chat_id, 'text': message},
            timeout=5,
        )
        if resp.status_code != 200:
            print(f'[Telegram] HTTP {resp.status_code}: {resp.text}')
            return
        data = resp.json()
        if not data.get('ok'):
            print(f'[Telegram] API error: {data}')
            return
        print('[Telegram] notification sent')
    except Exception as e:
        print(f'[Telegram] notification error: {e}')


limiter = Limiter(key_func=get_remote_address, default_limits=[])
app = FastAPI(title='AI Automation Leads API', version='1.0.0')
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(CORSMiddleware, allow_origins=['*'], allow_credentials=True, allow_methods=['*'], allow_headers=['*'])
security = HTTPBasic()


class LeadCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=100)
    business_name: str = Field(..., min_length=2, max_length=150)
    email: EmailStr
    phone: str = Field(..., min_length=7, max_length=25)
    message: str = Field(..., min_length=10, max_length=2000)
    service_interest: str = Field(..., min_length=2, max_length=100)

    class Config:
        anystr_strip_whitespace = True


class LeadResponse(BaseModel):
    id: int
    name: str
    business_name: str
    email: str
    phone: str
    message: str
    service_interest: str
    submitted_at: str


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    try:
        conn.execute('CREATE TABLE IF NOT EXISTS leads (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, business_name TEXT NOT NULL, email TEXT NOT NULL, phone TEXT NOT NULL, message TEXT NOT NULL, service_interest TEXT NOT NULL, submitted_at TEXT NOT NULL)')
        conn.commit()
    finally:
        conn.close()


def require_admin(credentials: HTTPBasicCredentials = Depends(security)):
    if not (secrets.compare_digest(credentials.username, ADMIN_USER) and secrets.compare_digest(credentials.password, ADMIN_PASS)):
        raise HTTPException(status_code=401, detail='Invalid credentials', headers={'WWW-Authenticate': 'Basic'})
    return credentials.username


@app.on_event('startup')
def startup():
    init_db()
    send_telegram_notification('Bot is online! Lead notifications are active.')


@app.get('/health')
def health():
    return {'status': 'ok'}


@app.post('/leads', status_code=201)
@limiter.limit('5/hour')
async def submit_lead(request: Request, lead: LeadCreate):
    submitted_at = datetime.now(timezone.utc).isoformat()
    conn = get_db()
    try:
        cursor = conn.execute(
            'INSERT INTO leads (name, business_name, email, phone, message, service_interest, submitted_at) VALUES (?, ?, ?, ?, ?, ?, ?)',
            (lead.name, lead.business_name, lead.email, lead.phone, lead.message, lead.service_interest, submitted_at)
        )
        conn.commit()
        lead_id = cursor.lastrowid
    finally:
        conn.close()
    send_telegram_notification(
        f'New Lead!\n\nName: {lead.name}\nBusiness: {lead.business_name}\nEmail: {lead.email}\nPhone: {lead.phone}\nService: {lead.service_interest}\n\nMessage: {lead.message}'
    )
    return {'success': True, 'message': 'Thanks! We will be in touch within 24 hours.', 'lead_id': lead_id}


@app.get('/leads')
def get_leads(admin: str = Depends(require_admin)):
    conn = get_db()
    try:
        rows = conn.execute('SELECT * FROM leads ORDER BY submitted_at DESC').fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


if __name__ == '__main__':
    import uvicorn
    uvicorn.run('leads_api:app', host=API_HOST, port=API_PORT, reload=False)
