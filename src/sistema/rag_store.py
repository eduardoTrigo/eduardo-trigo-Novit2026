"""Persistencia y verificación de versiones completas del índice RAG."""

import json
from collections import Counter

from psycopg.types.json import Jsonb

from sistema.postgres import ConfiguracionPostgres, conectar_postgres
from sistema.rag_corpus import (
    VERSION_PREPARACION,
    CorpusPreparado,
    ErrorCorpus,
    huella,
    identidad_corpus,
    json_canonico,
)
from sistema.rag_embeddings import ErrorEmbedding, ModeloEmbedding, ProveedorEmbedding, generar_vectores


TIMEOUT_SENTENCIA_MS = 10_000
MAX_COMPONENTES = 3_000_000
CAMPOS_CHUNK = {"id", "documento", "texto", "texto_embedding", "sha_texto"}


def _conexion(config):
    return conectar_postgres(config, timeout_sentencia_ms=TIMEOUT_SENTENCIA_MS)


def _hash_preparado(documentos, chunks):
    return huella(json_canonico({
        "documentos": [huella(json_canonico(d)) for d in documentos],
        "chunks": [huella(json_canonico(c)) for c in chunks],
    }))


def ingestar(
    config: ConfiguracionPostgres, corpus: CorpusPreparado,
    *, proveedor: ProveedorEmbedding | None = None, modelo: ModeloEmbedding | None = None,
) -> bool:
    """Publica una versión nueva de forma atómica y devuelve si hubo cambios."""
    if (proveedor is None) != (modelo is None):
        raise ErrorEmbedding("Se requieren un proveedor y la identidad esperada del modelo")
    if modelo is not None:
        modelo = modelo.model_copy(deep=True)
        if proveedor.modelo.identidad != modelo.identidad:
            raise ErrorEmbedding("Proveedor incompatible con el espacio vectorial solicitado")

    identidad = corpus.identidad
    espacio = modelo.identidad if modelo else None
    documentos = [d.model_dump(mode="json") for d in corpus.documentos]
    chunks = [c.model_dump(mode="json") for c in corpus.chunks]
    preparado = _hash_preparado(documentos, chunks)
    with _conexion(config) as db:
        existente = db.execute(
            "SELECT sha_preparado FROM rag.corpus WHERE id = %s", (identidad,),
        ).fetchone()
        if existente and existente[0] != preparado:
            raise ErrorCorpus("La preparación cambió para la misma versión del corpus")
        if existente and (
            espacio is None or db.execute(
                "SELECT 1 FROM rag.indice WHERE corpus_id = %s AND espacio = %s", (identidad, espacio),
            ).fetchone()
        ):
            return False

    vectores = []
    if proveedor is not None:
        if len(chunks) * modelo.dimensiones > MAX_COMPONENTES:
            raise ErrorEmbedding("La indexación excede el presupuesto de componentes vectoriales")
        vectores = generar_vectores(proveedor, modelo, [c["texto_embedding"] for c in chunks])

    with _conexion(config) as db:
        nuevo = db.execute(
            """
            INSERT INTO rag.corpus (id, preparacion, manifiesto, sha_preparado, documentos, chunks)
            VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (id) DO NOTHING RETURNING id
            """,
            (identidad, VERSION_PREPARACION, Jsonb(corpus.manifiesto.model_dump(mode="json")),
             preparado, len(documentos), len(chunks)),
        ).fetchone()
        if nuevo:
            _insertar_documentos(db, identidad, documentos, chunks)
        elif db.execute("SELECT sha_preparado FROM rag.corpus WHERE id = %s", (identidad,)).fetchone()[0] != preparado:
            raise ErrorCorpus("La preparación cambió para la misma versión del corpus")
        nuevo_indice = _insertar_indice(db, identidad, modelo, chunks, vectores) if modelo else False
        return bool(nuevo or nuevo_indice)


def _insertar_documentos(db, identidad, documentos, chunks):
    with db.cursor() as cursor:
        cursor.executemany(
            "INSERT INTO rag.documento (corpus_id, id, metadatos) VALUES (%s, %s, %s)",
            ((identidad, d["id"], Jsonb({k: v for k, v in d.items() if k != "id"})) for d in documentos),
        )
        cursor.executemany(
            """
            INSERT INTO rag.chunk (corpus_id, id, documento_id, metadatos, texto, texto_embedding, sha_texto)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            ((identidad, c["id"], c["documento"],
              Jsonb({k: v for k, v in c.items() if k not in CAMPOS_CHUNK}),
              c["texto"], c["texto_embedding"], c["sha_texto"]) for c in chunks),
        )


def _insertar_indice(db, identidad, modelo, chunks, vectores):
    espacio = modelo.identidad
    nuevo = db.execute(
        """
        INSERT INTO rag.indice (corpus_id, espacio, ambito, dimensiones, modelo)
        VALUES (%s, %s, %s, %s, %s) ON CONFLICT (corpus_id, espacio) DO NOTHING RETURNING espacio
        """,
        (identidad, espacio, modelo.ambito, modelo.dimensiones, Jsonb(modelo.model_dump(mode="json"))),
    ).fetchone()
    if nuevo:
        with db.cursor() as cursor:
            cursor.executemany(
                """
                INSERT INTO rag.embedding (corpus_id, espacio, chunk_id, dimensiones, vector)
                VALUES (%s, %s, %s, %s, %s::public.vector)
                """,
                ((identidad, espacio, chunk["id"], modelo.dimensiones, json.dumps(vector))
                 for chunk, vector in zip(chunks, vectores, strict=True)),
            )
    return bool(nuevo)


def consultar_estado(
    config: ConfiguracionPostgres, corpus_id: str | None = None, *, verificar: bool = False,
) -> list[dict]:
    """Obtiene estadísticas y, si se solicita, recalcula las huellas persistidas."""
    with _conexion(config) as db:
        db.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        registros = db.execute(
            """
            SELECT id, preparacion, manifiesto, sha_preparado, documentos, chunks
              FROM rag.corpus WHERE %s::text IS NULL OR id = %s ORDER BY ingresado_en, id
            """,
            (corpus_id, corpus_id),
        ).fetchall()
        if corpus_id and not registros:
            raise ErrorCorpus("El corpus solicitado no está ingresado")
        return [_estado_corpus(db, registro, verificar) for registro in registros]


def _estado_corpus(db, registro, verificar):
    identidad, preparacion, manifiesto, esperado, num_documentos, num_chunks = registro
    documentos = [
        dict(datos, id=id_doc) for id_doc, datos in db.execute(
            'SELECT id, metadatos FROM rag.documento WHERE corpus_id = %s ORDER BY id COLLATE "C"',
            (identidad,),
        )
    ]
    if verificar:
        chunks = [
            dict(datos, id=id_chunk, documento=id_doc, texto=texto, texto_embedding=entrada, sha_texto=sha)
            for id_chunk, id_doc, datos, texto, entrada, sha in db.execute(
                """
                SELECT id, documento_id, metadatos, texto, texto_embedding, sha_texto
                  FROM rag.chunk WHERE corpus_id = %s ORDER BY id COLLATE "C"
                """, (identidad,),
            )
        ]
        if (
            identidad_corpus(manifiesto, preparacion) != identidad
            or esperado != _hash_preparado(documentos, chunks)
            or any(c["sha_texto"] != huella(c["texto"].encode()) for c in chunks)
        ):
            raise ErrorCorpus("Las huellas del corpus persistido no coinciden")

    conteos = Counter(dict(db.execute(
        "SELECT documento_id, count(*) FROM rag.chunk WHERE corpus_id = %s GROUP BY documento_id",
        (identidad,),
    ).fetchall()))
    total_chunks = conteos.total()
    if num_documentos != len(documentos) or num_chunks != total_chunks:
        raise ErrorCorpus("La cantidad de documentos o chunks persistidos es inconsistente")

    espacios = _estado_espacios(db, identidad, total_chunks, verificar)
    vigencia_chunks = Counter()
    for documento in documentos:
        vigencia_chunks[_vigencia(documento["vigente"])] += conteos[documento["id"]]
    return {
        "corpus": identidad, "preparacion": preparacion, "manifiesto": manifiesto,
        "documentos_tecnicos": sum(d["tipo"] != "historial" for d in documentos),
        "documentos_historial": sum(d["tipo"] == "historial" for d in documentos),
        "secciones": total_chunks - conteos["historial"], "ordenes": conteos["historial"],
        "chunks": total_chunks, "chunks_por_documento": dict(sorted(conteos.items())),
        "documentos_por_vigencia": dict(Counter(_vigencia(d["vigente"]) for d in documentos)),
        "chunks_por_vigencia": dict(vigencia_chunks),
        "embeddings_operativos": sum(e["embeddings"] for e in espacios if e["ambito"] == "operativo"),
        "embeddings_prueba": sum(e["embeddings"] for e in espacios if e["ambito"] == "prueba"),
        "espacios": espacios,
    }


def _vigencia(vigente):
    return {True: "vigente", False: "no_vigente", None: "sin_vigencia"}[vigente]


def _estado_espacios(db, identidad, total_chunks, verificar):
    espacios = []
    for espacio, ambito, dimensiones, modelo, cantidad in db.execute(
        """
        SELECT i.espacio, i.ambito, i.dimensiones, i.modelo, count(e.chunk_id)
        FROM rag.indice i LEFT JOIN rag.embedding e USING (corpus_id, espacio, dimensiones)
        WHERE i.corpus_id = %s GROUP BY i.corpus_id, i.espacio ORDER BY i.espacio
        """, (identidad,),
    ):
        if verificar and (cantidad != total_chunks or espacio != ModeloEmbedding.model_validate(modelo).identidad):
            raise ErrorCorpus("El espacio vectorial persistido es incompleto o incompatible")
        espacios.append({
            "espacio": espacio, "ambito": ambito, "dimensiones": dimensiones, "modelo": modelo,
            "embeddings": cantidad, "completo": cantidad == total_chunks,
        })
    return espacios
