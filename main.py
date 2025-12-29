from fastapi import FastAPI, Depends, HTTPException
from fastapi.security import HTTPBearer
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Boolean, Float, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from pydantic import BaseModel
from jose import JWTError, jwt
from passlib.context import CryptContext
from datetime import datetime, timedelta
import openai
import os

app = FastAPI(title="ANCE.AI")
security = HTTPBearer()
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
openai.api_key = os.getenv("OPENAI_API_KEY")

# Database (Railway auto-provides)
SQLALCHEMY_DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./test.db")
engine = create_engine(SQLALCHEMY_DATABASE_URL)
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    email = Column(String, unique=True)
    hashed_password = Column(String)
    is_active = Column(Boolean, default=True)

class Subscription(Base):
    __tablename__ = "subscriptions"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    status = Column(String, default="active")
    cycle_end = Column(DateTime)
    tokens_used = Column(Float, default=0.0)
    quota = Column(Float, default=500.0)

Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try: yield db
    finally: db.close()

class UserCreate(BaseModel):
    email: str
    password: str

class ChatRequest(BaseModel):
    message: str
    type: str = "text"

@app.get("/", response_class=HTMLResponse)
async def read_root():
    with open("index.html") as f:
        return HTMLResponse(content=f.read())

@app.post("/register")
async def register(user: UserCreate, db=Depends(get_db)):
    if db.query(User).filter(User.email == user.email).first():
        raise HTTPException(400, "Email already registered")
    hashed = pwd_context.hash(user.password)
    db_user = User(email=user.email, hashed_password=hashed)
    db.add(db_user)
    db.commit()
    sub = Subscription(user_id=db_user.id, cycle_end=datetime.utcnow() + timedelta(days=30))
    db.add(sub)
    db.commit()
    return {"msg": "Registered! Login now."}

@app.post("/login")
async def login(user: UserCreate, db=Depends(get_db)):
    db_user = db.query(User).filter(User.email == user.email).first()
    if not db_user or not pwd_context.verify(user.password, db_user.hashed_password):
        raise HTTPException(400, "Invalid credentials")
    token = jwt.encode({"sub": db_user.id}, os.getenv("SECRET_KEY", "secret"), algorithm="HS256")
    return {"access_token": token}

@app.post("/chat")
async def chat(request: ChatRequest, token: str = Depends(security), db=Depends(get_db)):
    try:
        payload = jwt.decode(token.credentials, os.getenv("SECRET_KEY", "secret"), algorithms=["HS256"])
        user_id = payload["sub"]
    except: raise HTTPException(401, "Invalid token")
    
    sub = db.query(Subscription).filter(Subscription.user_id == user_id).first()
    if not sub or datetime.utcnow() > sub.cycle_end or sub.tokens_used >= sub.quota:
        raise HTTPException(403, "Quota exceeded")
    
    if request.type == "text":
        resp = openai.chat.completions.create(model="gpt-4o-mini", messages=[{"role": "user", "content": request.message}])
        tokens = resp.usage.total_tokens if resp.usage else 50
        return {"response": resp.choices[0].message.content, "tokens": tokens}
    return {"error": "Only text supported"}

@app.get("/usage")
async def usage(token: str = Depends(security), db=Depends(get_db)):
    payload = jwt.decode(token.credentials, os.getenv("SECRET_KEY", "secret"), algorithms=["HS256"])
    sub = db.query(Subscription).filter(Subscription.user_id == payload["sub"]).first()
    return {"used": sub.tokens_used, "quota": sub.quota}

app.mount("/", StaticFiles(directory=".", html=True), name="static")
