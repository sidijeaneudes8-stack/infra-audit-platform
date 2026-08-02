"""
Modèles SQLAlchemy pour la plateforme d'audit de réactivité d'agences immobilières.
Tables : agencies, audits.
"""
import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    String,
    DateTime,
    Integer,
    Text,
    Boolean,
    ForeignKey,
    Enum as SAEnum,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class AuditStatus(str, enum.Enum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"


class ProspectingStatus(str, enum.Enum):
    NON_CONTACTEE = "NON_CONTACTEE"      # audit pas encore terminé
    EN_COURS = "EN_COURS"                # audit terminé, prospection à démarrer (auto)
    APPELE = "APPELE"                    # premier contact effectué
    RELANCE_1 = "RELANCE_1"
    RELANCE_2 = "RELANCE_2"
    SIGNEE = "SIGNEE"
    PERDUE = "PERDUE"


class TestStatus(str, enum.Enum):
    SENT = "SENT"
    RESPONDED_HUMAN = "RESPONDED_HUMAN"
    RESPONDED_AUTO = "RESPONDED_AUTO"
    IGNORED = "IGNORED"


class Agency(Base):
    __tablename__ = "agencies"

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    name = Column(String, nullable=False)
    public_email = Column(String, nullable=False)
    catalog_url = Column(String, nullable=True)
    audit_status = Column(
        SAEnum(AuditStatus, name="audit_status_enum"),
        nullable=False,
        default=AuditStatus.PENDING,
    )
    created_at = Column(DateTime(timezone=True), default=_now)

    # --- Suivi commercial (prospection), géré depuis le dashboard ---
    prospecting_status = Column(
        SAEnum(ProspectingStatus, name="prospecting_status_enum"),
        nullable=False,
        default=ProspectingStatus.NON_CONTACTEE,
    )
    last_contact_at = Column(DateTime(timezone=True), nullable=True)
    relance_count = Column(Integer, nullable=False, default=0)
    prospecting_notes = Column(Text, nullable=True)

    audits = relationship("Audit", back_populates="agency", cascade="all, delete-orphan")
    properties = relationship("Property", back_populates="agency", cascade="all, delete-orphan")


class Property(Base):
    """Biens extraits du catalogue d'une agence lors du sourcing (find_leads),
    consommés ensuite par send_tests (1 bien par test_index, 1/2/3)."""

    __tablename__ = "properties"

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    agency_id = Column(UUID(as_uuid=False), ForeignKey("agencies.id"), nullable=False)

    test_index = Column(Integer, nullable=False)  # 1, 2 ou 3 : à quel test ce bien est réservé
    property_ref = Column(String, nullable=True)
    property_title = Column(String, nullable=False)
    property_url = Column(String, nullable=True)
    property_price = Column(String, nullable=True)

    used = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), default=_now)

    agency = relationship("Agency", back_populates="properties")


class Audit(Base):
    __tablename__ = "audits"

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    agency_id = Column(UUID(as_uuid=False), ForeignKey("agencies.id"), nullable=False)

    test_index = Column(Integer, nullable=False)  # 1, 2 ou 3
    property_ref = Column(String, nullable=True)
    property_title = Column(String, nullable=True)
    property_url = Column(String, nullable=True)

    sender_email = Column(String, nullable=False)
    inquiry_text = Column(Text, nullable=True)

    sent_at = Column(DateTime(timezone=True), nullable=True)
    received_at = Column(DateTime(timezone=True), nullable=True)
    response_text = Column(Text, nullable=True)
    latency_minutes = Column(Integer, nullable=True)
    is_human_response = Column(Boolean, nullable=True)

    test_status = Column(
        SAEnum(TestStatus, name="test_status_enum"),
        nullable=False,
        default=TestStatus.SENT,
    )
    created_at = Column(DateTime(timezone=True), default=_now)

    agency = relationship("Agency", back_populates="audits")
