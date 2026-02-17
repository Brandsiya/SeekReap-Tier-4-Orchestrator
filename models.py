from sqlalchemy import Column, String, Boolean, Float, ForeignKey, TIMESTAMP, Text
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.sql import func
import uuid
from database import Base

class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = Column(String, unique=True, nullable=False)
    hashed_password = Column(Text, nullable=False)
    created_at = Column(TIMESTAMP, server_default=func.now())


class OAuthAccount(Base):
    __tablename__ = "oauth_accounts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    platform = Column(String, nullable=False)
    access_token = Column(Text, nullable=False)
    refresh_token = Column(Text)
    expires_at = Column(TIMESTAMP)
    created_at = Column(TIMESTAMP, server_default=func.now())


class CreatorProfile(Base):
    __tablename__ = "creator_profiles"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    platform = Column(String, nullable=False)
    platform_channel_id = Column(String, nullable=False)
    channel_name = Column(String)
    verified_status = Column(Boolean, default=False)
    created_at = Column(TIMESTAMP, server_default=func.now())


class AnalyticsSnapshot(Base):
    __tablename__ = "analytics_snapshots"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    creator_id = Column(UUID(as_uuid=True), ForeignKey("creator_profiles.id", ondelete="CASCADE"))
    raw_data = Column(JSONB, nullable=False)
    verified_score = Column(Float)
    collected_at = Column(TIMESTAMP, server_default=func.now())
