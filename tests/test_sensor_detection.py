from dataclasses import replace
from datetime import datetime, timedelta, timezone
from math import inf, nan

import pytest
from planta_contrato import Calidad, LecturaSensor

from sistema.sensor_detection import LimitesSensor, Serie, avanzar, evaluar, intervalo_historial


INICIO = datetime(2026, 11, 20, 15, tzinfo=timezone.utc)
CLAVE = ("B-101", "presion_descarga")
LIMITES = LimitesSensor("kg/cm2", baja=95, alta=125, tipo_equipo="bomba")


def ts(segundos):
    return INICIO + timedelta(seconds=segundos)


def lectura(segundos, valor, *, clave=CLAVE, unidad="kg/cm2", calidad=Calidad.buena):
    return LecturaSensor(tag=clave[0], variable=clave[1], ts=ts(segundos), valor=valor,
                         unidad=unidad, calidad=calidad)


def avanzar_con(serie, segundos, valor, *, estado="marcha", limites=LIMITES, historial=(), epoca="prueba"):
    punto = evaluar(lectura(segundos, valor), limites, estado, en_recuperacion=serie.activo is not None)
    return avanzar(serie, punto, epoca=epoca, clave=CLAVE, huella=limites.huella, historial=historial)


def reproducir(pares, serie=Serie(), **kwargs):
    pasos = []
    for segundos, valor in pares:
        paso = avanzar_con(serie, segundos, valor, **kwargs)
        pasos.append(paso)
        serie = paso.serie
    return pasos


@pytest.mark.parametrize("pares", [
    [(0, 110), (60, 110), (120, 110)],
    [(0, 110), (60, 125), (120, 110)],
    [(0, 125), (59, 125), (119, 125)],
    [(0, 125), (0, 125), (120, 125)],
    [(0, 125), (60, 125), (240, 125)],
])
def test_normal_pico_duracion_repetidos_y_huecos_no_abren(pares):
    assert all(p.apertura is None for p in reproducir(pares))


@pytest.mark.parametrize(("valor", "direccion"), [(125, "alta"), (95, "baja")])
def test_tres_instantes_sostenidos_umbral_inclusivo(valor, direccion):
    pasos = reproducir([(0, valor), (60, valor), (120, valor), (180, valor)])
    assert [p.apertura is not None for p in pasos] == [False, False, True, False]
    apertura = pasos[2].apertura
    assert apertura.direccion == direccion
    assert (apertura.inicio, apertura.apertura) == (ts(0), ts(120))
    assert len(apertura.evidencia) == 3


def test_tardia_no_avanza_y_conflicto_reinicia_candidatura():
    pasos = reproducir([(0, 125), (60, 125), (30, 125)])
    assert pasos[-1].serie.ultimo.ts == ts(60)
    assert pasos[-1].serie.motivo == "muestra_tardia"
    conflicto = avanzar_con(pasos[-1].serie, 0, 110)
    assert conflicto.serie.motivo == "muestra_contradictoria"
    assert conflicto.serie.ventana == ()
    assert avanzar_con(conflicto.serie, 120, 125).apertura is None


@pytest.mark.parametrize(("valor", "abre"), [(130, True), (110, False), (None, False)])
def test_hueco_requiere_cobertura_sostenida_de_historian(valor, abre):
    primera = avanzar_con(Serie(), 0, 130)
    actual = evaluar(lectura(180, 130), LIMITES, None)
    assert intervalo_historial(primera.serie, actual, LIMITES.huella) == (ts(0), ts(180))
    historico = [evaluar(lectura(60, valor), LIMITES, None, origen="historian"),
                 evaluar(lectura(120, 130), LIMITES, None, origen="historian")]
    paso = avanzar_con(primera.serie, 180, 130, historial=historico)
    assert (paso.apertura is not None) is abre
    if abre:
        assert [p.origen for p in paso.apertura.evidencia] == ["mqtt", "historian", "historian", "mqtt"]


def test_salto_acotado_y_sin_backfill_al_arrancar():
    actual = evaluar(lectura(1800, 130), LIMITES, None)
    assert intervalo_historial(Serie(), actual, LIMITES.huella) is None
    inicial = avanzar_con(Serie(), 0, 130).serie
    assert intervalo_historial(inicial, actual, LIMITES.huella) == (ts(1200), ts(1800))
    historial = [evaluar(lectura(t, 130), LIMITES, None, origen="historian") for t in (1680, 1740)]
    paso = avanzar_con(inicial, 1800, 130, historial=historial)
    assert paso.apertura.inicio == ts(1680)


def test_salto_largo_reancla_en_tramo_final_sostenido():
    inicial = avanzar_con(Serie(), 0, 130).serie
    historial = [
        evaluar(lectura(t, v), LIMITES, None, origen="historian")
        for t, v in [(1500, 110), (1560, None), (1680, 130), (1740, 130)]
    ]
    apertura = avanzar_con(inicial, 1800, 130, historial=historial).apertura
    assert apertura.inicio == ts(1680)
    assert [p.ts for p in apertura.evidencia] == [ts(1680), ts(1740), ts(1800)]


@pytest.mark.parametrize(("valor", "unidad", "calidad", "motivo"), [
    (130, "kg/cm2", Calidad.sin_dato, "sin_dato"),
    (None, "kg/cm2", Calidad.buena, "valor_invalido"),
    (nan, "kg/cm2", Calidad.buena, "valor_invalido"),
    (inf, "kg/cm2", Calidad.buena, "valor_invalido"),
    (-inf, "kg/cm2", Calidad.buena, "valor_invalido"),
    (130, "m", Calidad.buena, "unidad_incompatible"),
])
def test_dato_invalido_interrumpe_y_no_es_normal(valor, unidad, calidad, motivo):
    primera = avanzar_con(Serie(), 0, 130).serie
    punto = evaluar(lectura(60, valor, unidad=unidad, calidad=calidad), LIMITES, "marcha")
    paso = avanzar(primera, punto, epoca="prueba", clave=CLAVE, huella=LIMITES.huella)
    assert punto.nivel == "indeterminado" and punto.motivo == motivo
    assert paso.serie.ventana == ()
    assert avanzar_con(paso.serie, 120, 130).apertura is None


@pytest.mark.parametrize(("estado", "nivel"), [
    ("parada", "suspendido"), ("reserva", "suspendido"), ("mantenimiento", "suspendido"),
    ("marcha", "baja"), ("disparo", "baja"), (None, "indeterminado"),
])
def test_baja_depende_del_estado_operativo(estado, nivel):
    assert evaluar(lectura(0, 90), LIMITES, estado).nivel == nivel
    assert evaluar(lectura(0, 130), LIMITES, estado).nivel == "alta"


def test_reserva_entra_en_marcha_y_baja_historica_no_hereda_estado():
    reserva = avanzar_con(Serie(), 0, 90, estado="parada").serie
    pasos = reproducir([(60, 90), (120, 90), (180, 90)], reserva)
    assert pasos[-1].apertura.inicio == ts(60)
    historico = evaluar(lectura(120, 90), LIMITES, None, origen="historian")
    assert avanzar_con(pasos[0].serie, 320, 90, historial=[historico]).apertura is None


def test_recuperacion_sostenida_y_nuevo_episodio():
    activo = reproducir([(0, 125), (60, 125), (120, 125)])[-1].serie
    pasos = reproducir([(180, 110), (240, 110), (250, 90), (310, 90), (370, 90)], activo)
    assert all(p.cierre is None and p.apertura is None for p in pasos)
    assert pasos[-1].serie.activo.id == activo.activo.id
    pasos = reproducir([(430, 110), (490, 110), (550, 110)], pasos[-1].serie)
    assert pasos[-1].cierre.id == activo.activo.id
    assert pasos[-1].serie.activo is None
    otro = reproducir([(610, 90), (670, 90), (730, 90)], pasos[-1].serie)[-1].apertura
    assert otro.id != activo.activo.id and otro.direccion == "baja"


def test_no_recupera_durante_mantenimiento_o_estado_desconocido():
    activo = reproducir([(0, 125), (60, 125), (120, 125)])[-1].serie
    for estado in ("mantenimiento", None):
        pasos = reproducir([(180, 110), (240, 110), (300, 110)], activo, estado=estado)
        assert pasos[-1].serie.activo.id == activo.activo.id


def test_identidad_usa_epoca_y_cambio_de_limites_reinicia_ventana():
    lecturas = [(0, 130), (60, 130), (120, 130)]
    pasos = reproducir(lecturas)
    assert reproducir(lecturas)[-1].apertura.id == pasos[-1].apertura.id
    assert reproducir(lecturas, epoca="otra")[-1].apertura.id != pasos[-1].apertura.id
    cambio = avanzar_con(pasos[1].serie, 120, 130, limites=replace(LIMITES, alta=126))
    assert cambio.apertura is None
    assert [p.ts for p in cambio.serie.ventana] == [ts(120)]


def test_conversion_preserva_origen_y_canonico():
    punto = evaluar(lectura(0, 126 * 14.2233, unidad="psi"), LIMITES, "marcha")
    assert punto.nivel == "alta" and punto.valor_canonico == pytest.approx(126)
    assert punto.unidad == "psi" and punto.unidad_canonica == "kg/cm2"
    assert (punto.alarma_baja, punto.alarma_alta) == (95, 125)
