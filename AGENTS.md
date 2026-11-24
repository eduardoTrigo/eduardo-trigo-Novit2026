# Instrucciones para trabajar en PIA-3

## Antes de editar

1. Leer [docs/PLAN.md](docs/PLAN.md) y el Issue asignado. Consultar la [arquitectura](docs/ARQUITECTURA.md), las [decisiones](docs/DECISIONES_TECNICAS.md) y las fuentes oficiales pertinentes.
2. Inspeccionar `git status`, la rama activa y los archivos relacionados. Trabajar solamente en la rama del Issue. Si la rama difiere o hay cambios locales que puedan cruzarse con la tarea, informar lo observado antes de editar y preservar el trabajo existente.
3. Confirmar el alcance con el contenido del Issue y el Anexo de la entrega. Verificar cómo se ejecuta realmente la funcionalidad y qué contratos usa.

## Al implementar

- Respetar los tópicos, payloads y modelos del paquete oficial `planta_contrato`. Usar el Enunciado y el Anexo para los requisitos y consultar la rúbrica para entender su medición.
- Queda prohibido usar `/api/verdad` dentro del runtime del agente. Reservar el endpoint para evaluación y desarrollo controlados. El corrector oficial restringe el acceso y el agente debe funcionar sin depender de ese endpoint.
- Reutilizar los módulos existentes. Hacer cambios pequeños, reversibles y acotados a los archivos del Issue.
- Justificar y obtener autorización humana antes de instalar dependencias.
- Aplicar TDD cuando corresponda a un comportamiento nuevo o a una regresión reproducible. Ejecutar las pruebas pertinentes al alcance y respetar las restricciones del Issue. Para cambios exclusivamente documentales, validar las referencias, la coherencia y el diff.
- Tratar los datos persistentes como datos del usuario. Acordar el entorno y los permisos antes de ejecutar el corrector si puede interferir con la planta o sus volúmenes.

## Antes de entregar

- Revisar el diff y ejecutar `git diff --check`. Verificar el alcance y la presencia de todos los entregables.
- Informar los archivos modificados, pruebas y resultados, verificaciones pendientes y riesgos. Distinguir inspección de código, pruebas automatizadas y evidencia con el simulador.
- Obtener autorización humana antes de hacer commits, push, merge o eliminar datos.
