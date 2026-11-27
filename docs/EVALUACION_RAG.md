# Evaluación oficial de recuperación · Issue #25

## Reproducción

La corrida del 27 de noviembre de 2026 usó la suite de 71 preguntas y el evaluador `corpus.evaluar.evaluar(buscar, suite)` del simulador `ab65b95e911731351dba395817a74a8cc60da2b2`. El adaptador devuelve citas únicas en orden. El evaluador solicita hasta 20 para calcular MRR y usa los cortes 1, 3, 5 y 10. El script verifica las huellas del evaluador, las preguntas y los fragmentos y comprueba el corpus de PostgreSQL antes de comparar las variantes. [Evaluador oficial](https://github.com/NovitSoftware/academia2026-tp/blob/ab65b95e911731351dba395817a74a8cc60da2b2/planta/corpus/evaluar.py)

| Recurso | SHA-256 |
| --- | --- |
| Evaluador | `c9f3a3d2996fea8f95a1cd337a1cf7afbecafad2d443c7bcdc6fa533646ac19b` |
| Suite | `f464ea868cabe9971882975530a928e6590a36166c4be1bc2e6568cf4045a9d5` |
| Fragmentos | `a07975aaafd560c7017384c3590237b0cacb5a62ce002847e7bfafd576fd8a81` |

Desde el repositorio, con el entorno Python activo y variables `POSTGRES_*` del rol de aplicación, apunta a una instancia que tenga el corpus indicado. El procedimiento de ingesta describe cómo prepararla en un proyecto aislado. El informe JSON se escribe en una ruta nueva de `tmp/` y contiene métricas por variante, categoría y pregunta, las citas recuperadas y las latencias. La suite reside en el checkout oficial y se lee solo para evaluar. [Configuración PostgreSQL aislada](SPEC_RAG_INDEXACION.md#verificación-aislada)

```bash
PYTHONPATH=src python scripts/evaluar_rag_retrieval.py \
  --simulador ../academia2026-tp \
  --corpus-id 83cfec46e9d62b88fcf0ee2767f794fa0fd64ce7277ad41e1d0ccfd20991d1a2 \
  --salida tmp/evaluacion-rag-issue25.json
```

BM25 se ejecutó primero sobre el texto oficial completo de 701 chunks. FTS base usó los mismos 701 chunks con diccionario español, lexemas unidos por OR y `ts_rank_cd`. FTS operativo evaluó las mismas preguntas y excluyó 108 secciones de la Rev. C y 6 secciones adicionales marcadas como ajenas a PIA-3. El universo elegible operativo quedó en 587 chunks e incluyó el historial. Las 71 preguntas miden el desempeño en esa suite. Las pruebas de regresión independientes cubren citas, equipos y códigos, y la generalización queda pendiente de una evaluación separada. [Ranking y filtros](SPEC_RAG_RETRIEVAL.md)

## Resultados globales

| Métrica | BM25 oficial | FTS base | FTS operativo | Semántica | Híbrida |
| --- | ---: | ---: | ---: | --- | --- |
| Hit@1 | 0,352 | 0,127 | 0,549 | Pendiente | Pendiente |
| Hit@3 | 0,775 | 0,394 | 0,746 | Pendiente | Pendiente |
| Hit@5 | 0,845 | 0,493 | 0,831 | Pendiente | Pendiente |
| Hit@10 | 0,930 | 0,704 | 0,901 | Pendiente | Pendiente |
| Recall@1 | 0,251 | 0,106 | 0,385 | Pendiente | Pendiente |
| Recall@3 | 0,601 | 0,308 | 0,592 | Pendiente | Pendiente |
| Recall@5 | 0,664 | 0,378 | 0,697 | Pendiente | Pendiente |
| Recall@10 | 0,772 | 0,585 | 0,789 | Pendiente | Pendiente |
| MRR | 0,573 | 0,313 | 0,665 | Pendiente | Pendiente |
| Distractor@1 | 0,056 | 0,014 | 0,000 | Pendiente | Pendiente |
| Distractor@3 | 0,085 | 0,028 | 0,028 | Pendiente | Pendiente |
| Distractor@5 | 0,099 | 0,042 | 0,028 | Pendiente | Pendiente |
| Distractor@10 | 0,099 | 0,113 | 0,028 | Pendiente | Pendiente |

La reproducción BM25 dio 0,845 de Hit@5 y 0,664 de Recall@5, coherentes con 0,85 y 0,66 publicados tras redondear. FTS operativo tuvo mayor Recall@5 y MRR y menor Distractor@5. BM25 tuvo mayor Hit@3, Hit@5 y Hit@10. Coseno y RRF quedan pendientes de un modelo autorizado por Novit. [Referencia oficial BM25](https://github.com/NovitSoftware/academia2026-tp/blob/ab65b95e911731351dba395817a74a8cc60da2b2/datos/documentos/README.md)

### Resultados por categoría

Cada celda indica Hit@5 / Recall@5 / MRR.

| Categoría | Preguntas | BM25 | FTS base | FTS operativo |
| --- | ---: | ---: | ---: | ---: |
| Certificación | 5 | 1,000 / 0,900 / 0,700 | 1,000 / 1,000 / 0,667 | 1,000 / 1,000 / 0,867 |
| Contrato | 7 | 0,857 / 0,762 / 0,762 | 0,429 / 0,429 / 0,335 | 0,714 / 0,714 / 0,565 |
| Diagnóstico | 14 | 1,000 / 0,774 / 0,625 | 0,929 / 0,619 / 0,443 | 1,000 / 0,821 / 0,911 |
| Historial | 11 | 0,818 / 0,591 / 0,498 | 0,091 / 0,045 / 0,066 | 0,545 / 0,379 / 0,336 |
| Límites | 11 | 0,636 / 0,424 / 0,365 | 0,273 / 0,167 / 0,202 | 0,818 / 0,576 / 0,570 |
| Operación | 11 | 0,636 / 0,606 / 0,678 | 0,545 / 0,485 / 0,465 | 1,000 / 0,909 / 0,782 |
| Procedimiento | 8 | 1,000 / 0,646 / 0,396 | 0,125 / 0,062 / 0,134 | 0,625 / 0,438 / 0,516 |
| Seguridad | 4 | 1,000 / 0,875 / 0,750 | 0,750 / 0,500 / 0,307 | 1,000 / 1,000 / 0,875 |

### Distractores y revisión del manual

Distractor@k oficial cuenta preguntas con un distractor etiquetado en el top-k antes de la primera cita pertinente. El conteo de revisiones compara la primera aparición de cada revisión entre las primeras 20 citas cuando aparecen ambas. También se mide el caso restringido a citas C etiquetadas como distractor y citas D etiquetadas como relevantes para esa pregunta.

| Preguntas | BM25 oficial | FTS base | FTS operativo |
| --- | ---: | ---: | ---: |
| C aparece antes de D, cualquier cita | 59 | 55 | 0 |
| Aparece C y D queda fuera del top-20 | 2 | 0 | 0 |
| C distractora aparece antes de D relevante | 7 | 9 | 0 |
| Sin cita pertinente en top-10 | 5 | 21 | 7 |
| Sin cita pertinente en top-20 | 3 | 12 | 3 |

El 7/71 de BM25 en la comparación etiquetada reproduce aproximadamente una pregunta de cada diez. Los dos distractores de FTS operativo en top-5 corresponden a contrato.

### Aciertos y límites observados

- La búsqueda `TX-T07` recuperó `MBP-4 §6.1` como primer resultado con el filtro de documento en una prueba real de Docker. El mapa oficial da la correspondencia con `F09`.
- En diagnóstico de cavitación por ruido y baja succión, FTS operativo recuperó `TX350-D §8.9` primero y `MBP-4 §6.11` tercero. En certificación de horas recuperó `CERT-EJ §2.11` y `CERT-EJ §2.2` en las dos primeras posiciones.
- `pro-01` recibió órdenes de cambio de empaquetadura antes que el procedimiento buscado, que quedó fuera de las primeras 20 citas. `his-03` y `his-04` obtuvieron manuales pertinentes al tema y omitieron los antecedentes específicos etiquetados. `con-01` obtuvo la primera cita pertinente en la posición 16.
- FTS operativo mejoró la posición de la primera referencia en varios diagnósticos. Historial, contrato y parte de los procedimientos conservan dificultades. Los filtros explícitos de fuente permiten distinguir manuales, contratos y órdenes cuando esa intención ya se conoce.

## Latencia, recursos y entorno

| Variante | p50 | p95 |
| --- | ---: | ---: |
| BM25 oficial en memoria | 0,459 ms | 0,707 ms |
| FTS base | 46,422 ms | 77,590 ms |
| FTS operativo | 72,033 ms | 127,867 ms |

El tiempo FTS incluye conexión a PostgreSQL, SQL y validación de la respuesta. BM25 mide consultas en memoria luego de construir el índice local. PostgreSQL aislado quedó limitado a 0,5 CPU y 512 MiB y registró 35,07 MiB después de las pruebas. El contenedor de consulta tuvo 0,5 CPU, 256 MiB, cero montajes y una red interna Docker. Con el stack habitual del alumno activo, los límites asignados sumaron 4 CPU y 3,75 GiB para esa prueba. La imagen de verificación reutilizó la imagen existente del agente con los módulos de este Issue. Las pruebas de corpus, recuperación, PostgreSQL y memoria dieron 37 aprobados en el proyecto aislado.

La suite habitual con el corpus oficial obtuvo 526 aprobados y 19 omitidos. Las integraciones del catálogo y de MCP, incluidas las de stdio en Docker, obtuvieron 6 aprobados. También pasaron `docker compose --env-file .env.example config --quiet` y `git diff --check`.
