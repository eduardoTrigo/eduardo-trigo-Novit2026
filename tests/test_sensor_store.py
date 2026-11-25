import sqlite3
from contextlib import closing

import pytest

from sistema.sensor_store import AlmacenSensoresB1
from test_part_classification_integration import confirmar, consultar, trabajos
from test_part_workflow import preparar_borrador
from test_sensor_detection import CLAVE, avanzar_con, ts


def persistir(almacen, segundos, valor=130):
    anterior = almacen.leer(CLAVE)
    cambio = avanzar_con(anterior.serie, segundos, valor, epoca=almacen.epoca)
    assert almacen.guardar(CLAVE, anterior, cambio)
    return cambio


def episodios(almacen):
    return consultar(almacen, "SELECT * FROM episodios_sensor_b1 ORDER BY abierto")


def test_candidatura_y_episodio_se_recuperan_con_misma_identidad(tmp_path):
    ruta = tmp_path / "b1.sqlite"
    almacen = AlmacenSensoresB1(ruta, "turno")
    persistir(almacen, 0)
    persistir(almacen, 60)
    reabierto = AlmacenSensoresB1(ruta, "turno")
    assert [p.ts for p in reabierto.leer(CLAVE).serie.ventana] == [ts(0), ts(60)]
    reabierto.interrumpir("reinicio")
    assert reabierto.leer(CLAVE).serie.ventana == ()
    assert persistir(reabierto, 120).apertura is None
    persistir(reabierto, 180)
    apertura = persistir(reabierto, 240).apertura
    assert episodios(reabierto)[0]["id"] == apertura.id
    assert episodios(reabierto)[0]["publicacion"] == "pendiente"
    reiniciado = AlmacenSensoresB1(ruta, "turno")
    reiniciado.interrumpir("reinicio")
    assert reiniciado.leer(CLAVE).serie.activo.id == apertura.id
    persistir(reiniciado, 300)
    persistir(reiniciado, 360, 110)
    persistir(reiniciado, 420, 110)
    cierre = persistir(reiniciado, 480, 110).cierre
    assert cierre.id == apertura.id
    assert episodios(reiniciado)[0]["recuperado"] == ts(480).isoformat()
    assert len(episodios(reiniciado)) == 1
    assert reiniciado.leer(CLAVE).serie.activo is None
    assert AlmacenSensoresB1(ruta, "otro-turno").leer(CLAVE).serie.ultimo is None


def test_reintento_obsoleto_y_duplicados_conservan_una_decision(tmp_path):
    almacen = AlmacenSensoresB1(tmp_path / "b1.sqlite", "turno")
    persistir(almacen, 0)
    persistir(almacen, 60)
    anterior = almacen.leer(CLAVE)
    apertura = avanzar_con(anterior.serie, 120, 130, epoca=almacen.epoca)
    assert almacen.guardar(CLAVE, anterior, apertura)
    assert not almacen.guardar(CLAVE, anterior, apertura)
    assert persistir(almacen, 120).apertura is None
    assert len(episodios(almacen)) == 1


@pytest.mark.parametrize("accion", ["ABORT, 'fallo de prueba'", "IGNORE"])
def test_apertura_y_decision_pendiente_hacen_rollback_juntas(tmp_path, accion):
    almacen = AlmacenSensoresB1(tmp_path / "b1.sqlite", "turno")
    persistir(almacen, 0)
    persistir(almacen, 60)
    with closing(sqlite3.connect(almacen.ruta)) as db, db:
        db.execute(f"""
            CREATE TRIGGER impedir_apertura BEFORE INSERT ON episodios_sensor_b1
            BEGIN SELECT RAISE({accion}); END
        """)
    with pytest.raises(sqlite3.IntegrityError):
        persistir(almacen, 120)
    assert almacen.leer(CLAVE).serie.activo is None
    assert almacen.leer(CLAVE).serie.ultimo.ts == ts(60)
    assert episodios(almacen) == []
    with closing(sqlite3.connect(almacen.ruta)) as db, db:
        db.execute("DROP TRIGGER impedir_apertura")
    assert persistir(almacen, 120).apertura is not None
    assert episodios(almacen)[0]["publicacion"] == "pendiente"


def test_migracion_preserva_b1_partes_y_no_procesa_historico(tmp_path):
    nota, _, _, partes, worker = preparar_borrador(tmp_path)
    confirmar(nota, partes, worker)
    tablas = ("notas", "partes", "respuestas_parte", "trabajos_b1", "decisiones_b1", "outbox")
    anteriores = {t: consultar(partes, f"SELECT * FROM {t}") for t in tablas}
    almacen = AlmacenSensoresB1(partes.ruta, "turno")
    AlmacenSensoresB1(partes.ruta, "turno")
    assert {t: consultar(partes, f"SELECT * FROM {t}") for t in tablas} == anteriores
    assert trabajos(partes)[0]["estado"] == "pendiente"
    assert episodios(almacen) == []
