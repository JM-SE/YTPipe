from sqlalchemy import Boolean, String, true
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base, TimestampMixin


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    is_owner: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true(), unique=True, nullable=False)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)

    oauth_accounts = relationship("OAuthAccount", back_populates="user")
    user_channels = relationship("UserChannel", back_populates="user")
    notification_deliveries = relationship("NotificationDelivery", back_populates="user")
    sync_states = relationship("SyncState", back_populates="user")
