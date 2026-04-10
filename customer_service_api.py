import os, sqlite3
from contextlib import closing
from datetime import datetime, timezone
from typing import List, Optional
import openai
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
DB_PATH = os.getenv('CUSTOMER_SERVICE_DB_PATH', 'customer_service.db')
API_HOST = os.getenv('API_HOST', '0.0.0.0')
API_PORT = int(os.getenv('API_PORT', '8002'))
openai.api_key = OPENAI_API_KEY

app = FastAPI(title='Customer Service Automation API', version='1.0.0')
app.add_middleware(CORSMiddleware, allow_origins=['*'], allow_credentials=True, allow_methods=['*'], allow_headers=['*'])

class ProcessMessageRequest(BaseModel):
    customer_name: str = Field(..., min_length=1, max_length=100)
    message: str = Field(..., min_length=1, max_length=2000)
    business_type: str = Field(default='nail salon')
    channel: Optional[str] = None
    class Config: anystr_strip_whitespace = True

class ProcessMessageResponse(BaseModel):
    reply: str
    message_id: int

class StoredMessage(BaseModel):
    id: int
    customer_name: str
    message: str
    reply: str
    business_type: str
    channel: Optional[str]
    created_at: str

def get_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with closing(get_db()) as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY AUTOINCREMENT, customer_name TEXT NOT NULL, message TEXT NOT NULL, reply TEXT NOT NULL, business_type TEXT NOT NULL, channel TEXT, created_at TEXT NOT NULL)')
        conn.commit()

def generate_reply(message: str, business_type: str) -> str:
    if not OPENAI_API_KEY:
        raise HTTPException(status_code=500, detail='OPENAI_API_KEY is not configured.')
    system_prompt = f'You are a helpful professional customer service assistant for a {business_type}. Reply in 1-3 sentences. Be friendly and brief. Offer to help book if relevant.'
    try:
        response = openai.ChatCompletion.create(model='gpt-3.5-turbo', messages=[{'role':'system','content':system_prompt},{'role':'user','content':message}], max_tokens=150, temperature=0.7)
        return response.choices[0].message['content'].strip()
    except openai.error.AuthenticationError:
        raise HTTPException(status_code=500, detail='Invalid OpenAI API key.')
    except openai.error.RateLimitError:
        raise HTTPException(status_code=429, detail='Rate limit reached.')
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'AI failed: {str(e)}')

@app.on_event('startup')
def startup(): init_db()

@app.get('/health')
def health(): return {'status': 'ok'}

@app.post('/process-message', response_model=ProcessMessageResponse, status_code=201)
def process_message(req: ProcessMessageRequest):
    reply = generate_reply(req.message, req.business_type)
    created_at = datetime.now(timezone.utc).isoformat()
    conn = get_db()
    try:
        cursor = conn.execute('INSERT INTO messages (customer_name, message, reply, business_type, channel, created_at) VALUES (?, ?, ?, ?, ?, ?)', (req.customer_name, req.message, reply, req.business_type, req.channel, created_at))
        conn.commit()
        return ProcessMessageResponse(reply=reply, message_id=cursor.lastrowid)
    finally: conn.close()

@app.get('/messages', response_model=List[StoredMessage])
def get_messages():
    conn = get_db()
    try: return [dict(r) for r in conn.execute('SELECT * FROM messages ORDER BY created_at DESC LIMIT 100').fetchall()]
    finally: conn.close()

if __name__ == '__main__':
    import uvicorn
    uvicorn.run('customer_service_api:app', host=API_HOST, port=API_PORT, reload=False)
