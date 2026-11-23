
"""Pruebas de entrada MQTT persistente."""

from unittest.mock import MagicMock

from sistema.note_store import AlmacenNotas
from sistema.persistent_ingress import crear_entrada_persistente
from test_security_messages import crear_nota


def test_guardar_nota_recibida(tmp_path):
    ruta = tmp_path / "notas.sqlite3"
    almacen = AlmacenNotas(ruta)
    recibir = crear_entrada_persistente(almacen)

    nota = crear_nota()

    assert recibir(nota) is True
    assert almacen.obtener_estado(nota.id) == "pendiente"

    # Comprobar que persiste al abrir otra instancia.
    recuperado = AlmacenNotas(ruta)

    assert recuperado.pendientes() == 1


def test_ignorar_nota_duplicada(tmp_path):
    almacen = AlmacenNotas(
        tmp_path / "notas.sqlite3"
    )
    recibir = crear_entrada_persistente(almacen)

    nota = crear_nota()

    assert recibir(nota) is True
    assert recibir(nota) is True

    # El duplicado no genera otra tarea.
    assert almacen.pendientes() == 1


def test_informar_error_de_persistencia():
    almacen = MagicMock()
    almacen.guardar.side_effect = OSError(
        "Disco no disponible"
    )

    recibir = crear_entrada_persistente(almacen)

    assert recibir(crear_nota()) is False
    almacen.guardar.assert_called_once()
