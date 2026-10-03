"""Default view for appointments calendar (Paso 30 UX #11)."""

from app.routes.web.appointments import DEFAULT_APPOINTMENTS_VIEW


def test_default_appointments_view_is_day() -> None:
    assert DEFAULT_APPOINTMENTS_VIEW == "day"
