"""Smoke tests de modelos scheduling (Paso 30 Fase A)."""

from app.models import (
    Appointment,
    BusinessHour,
    Professional,
    ProfessionalSpecialty,
    ProfessionalWorkingHour,
    ScheduleException,
    SchedulingService,
)
from app.models.membership import Membership
from app.schemas.scheduling import AppointmentStatus


def test_scheduling_models_registered_on_metadata() -> None:
    tables = {
        "professionals",
        "services",
        "professional_specialties",
        "schedule_exceptions",
        "business_hours",
        "professional_working_hours",
        "appointments",
    }
    metadata_tables = set(Appointment.metadata.tables.keys())
    assert tables.issubset(metadata_tables)


def test_appointment_status_enum_values() -> None:
    assert AppointmentStatus.scheduled == "scheduled"
    assert AppointmentStatus.cancelled == "cancelled"


def test_model_tablenames() -> None:
    assert Professional.__tablename__ == "professionals"
    assert SchedulingService.__tablename__ == "services"
    assert BusinessHour.__tablename__ == "business_hours"
    assert ProfessionalWorkingHour.__tablename__ == "professional_working_hours"
    assert ProfessionalSpecialty.__tablename__ == "professional_specialties"
    assert ScheduleException.__tablename__ == "schedule_exceptions"
    assert Appointment.__tablename__ == "appointments"


def test_membership_has_permissions_column() -> None:
    assert "permissions" in Membership.__table__.c
