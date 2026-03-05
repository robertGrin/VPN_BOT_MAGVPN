from sqlalchemy import Column, Integer, String, Boolean, DateTime, BigInteger
from sqlalchemy.orm import declarative_base

Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True, index=True)
    telegram_id = Column(BigInteger, unique=True, index=True)

class Device(Base):
    __tablename__ = "devices"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(BigInteger, index=True) 
    name = Column(String, default="VPN Ключ") 
    vpn_key = Column(String, nullable=True)
    subscription_end = Column(DateTime, nullable=True)
    is_paid = Column(Boolean, default=False)
    warned_expiry = Column(Boolean, default=False)
