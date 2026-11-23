
"""Pruebas de outbox persistente de seguridad."""

from sistema.security_outbox import OutboxSeguridad


def test_guardar_sin_duplicar(tmp_path):
    outbox = OutboxSeguridad(tmp_path / "test.sqlite3")

    assert outbox.guardar(
        "evento-001",
        "sistema/eventos",
        '{"tipo":"AlertaSeguridad"}',
    ) is True

    assert outbox.guardar(
        "evento-001",
        "sistema/eventos",
        '{"tipo":"AlertaSeguridad"}',
    ) is False

    assert outbox.pendientes() == 1


def test_confirmar_envio(tmp_path):
    outbox = OutboxSeguridad(tmp_path / "test.sqlite3")

    outbox.guardar("evento-002", "sistema/eventos", "{}")

    mensaje = outbox.reclamar()

    assert mensaje["id"] == "evento-002"
    assert outbox.confirmar("evento-002") is True
    assert outbox.pendientes() == 0
    assert outbox.reclamar() is None


def test_recuperar_envio_interrumpido(tmp_path):
    ruta = tmp_path / "test.sqlite3"

    primera = OutboxSeguridad(ruta)
    primera.guardar("evento-003", "sistema/eventos", "{}")
    primera.reclamar()

    # Simular reinicio del proceso.
    segunda = OutboxSeguridad(ruta)

    assert segunda.recuperar() == 1
    assert segunda.pendientes() == 1

    mensaje = segunda.reclamar()

    assert mensaje["id"] == "evento-003"
    assert segunda.fallar(
        mensaje["id"],
        "Sin conexión MQTT",
    ) is True

    assert segunda.pendientes() == 1
