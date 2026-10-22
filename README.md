# Horario · constructor de horarios USC

Aplicación web para montar el horario semanal de los grados de **Matemáticas** y
**Doble Grado en Matemáticas y Física** de la Universidade de Santiago de
Compostela. Eliges un grupo de expositiva, laboratorio y seminario por materia y
la aplicación valida solapes, calcula huecos libres, sugiere dónde mover una
clase y permite compartir o exportar el resultado.

> **Estado:** prototipo funcional de hackathon. Los datos incluidos son una
> *semana tipo* importada de los ficheros `horario.csv` / `forms_data.json` del
> repositorio. No sustituyen al calendario oficial de la USC.

![Vista de escritorio en tema claro](docs/workspace-light.png)

[Pantalla de inicio](docs/landing-light.png) · [Vista en tema oscuro](docs/workspace-dark.png)

## Funcionalidad

- **Catálogo serializable y versionado** en `data/catalog.json`: grados, cursos,
  asignaturas, grupos, aulas, edificios y sesiones con ids estables.
- **Selector de grupos** de expositiva, laboratorio y seminario con aviso
  instantáneo de solapes y de coincidencia con tus preferencias.
- **Calendario semanal** con bloques coloreados por materia, conflictos marcados
  y geometría a 30 minutos.
- **Generación automática** con un solver de restricciones: minimiza días y
  huecos, respeta franjas bloqueadas y, si hay optativas incompatibles, devuelve
  el mayor subconjunto que cabe sin solapes.
- **Huecos libres** por día con duración mínima configurable (30 min / 1 h / 1,5 h).
- **Modo «mover una clase»**: al pulsar una clase, busca huecos compatibles para
  el grupo y para el resto de tu horario, y permite copiar la propuesta.
- **Compartir** con un enlace corto (se guarda una instantánea en el servidor).
- **Exportar** a JSON, calendario `.ics` o PDF mediante impresión.
- **Tema claro/oscuro**, diseño responsive y persistencia local del horario.

## Stack

| Capa | Tecnología |
| --- | --- |
| Backend | Python 3.11+ · FastAPI · Pydantic v2 |
| Datos | JSON versionado con JSON Schema + validación referencial |
| Frontend | HTML + CSS + JavaScript ES modules, sin build step |
| Tests | pytest + FastAPI TestClient |

No hay dependencias de frontend ni proceso de compilación: FastAPI sirve la SPA
directamente.

## Arranque rápido

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt

# (opcional) regenerar el catálogo desde los ficheros legacy
python scripts/import_legacy.py

uvicorn backend.main:app --reload
```

Abre <http://127.0.0.1:8000>. La documentación interactiva de la API está en
<http://127.0.0.1:8000/docs>.

En macOS/Linux:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
uvicorn backend.main:app --reload
```

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest
```

Cubren integridad del catálogo, importación, detección de solapes, huecos,
generación para los nueve cursos, preferencias, enlaces compartidos, ICS y los
endpoints principales. La interfaz obtiene 100/100 en accesibilidad, buenas
prácticas y SEO con Lighthouse, tanto en tema claro como oscuro.

## Estructura

```text
backend/
  main.py          API FastAPI + servido de la SPA
  models.py        modelo Pydantic y validación referencial
  scheduling.py    solver, solapes, huecos, ICS-ready sessions
  store.py         índices del catálogo y persistencia de enlaces
  timeutil.py      horas HH:MM y días
  ids.py           slugs e ids estables
frontend/
  index.html
  css/styles.css
  js/app.js        estado, UI, generación, compartir, exportar
  js/timetable.js  render del calendario y layout de solapes
  js/api.js        cliente HTTP
data/
  catalog.json     catálogo canónico versionado
  shared_schedules/
scripts/
  import_legacy.py importador CSV/JSON -> catálogo
schema/
  catalog.schema.json
docs/
  data-model.md    decisiones de serialización y migración
tests/
```

## API

| Método | Ruta | Para qué |
| --- | --- | --- |
| `GET` | `/api/health` | estado y contadores |
| `GET` | `/api/catalog` | catálogo completo serializado |
| `GET` | `/api/catalog/export` | descargar el JSON canónico |
| `GET` | `/api/schema/catalog` | JSON Schema del catálogo |
| `GET` | `/api/degrees` | grados, cursos y metadatos |
| `GET` | `/api/years/{year_id}` | vista enriquecida de un curso (grupos + sesiones + aulas) |
| `POST` | `/api/schedules/validate` | solapes, grupos que faltan, huecos y estadísticas |
| `POST` | `/api/schedules/generate` | generar una combinación de grupos |
| `POST` | `/api/free-slots` | complemento libre de un conjunto de grupos |
| `POST` | `/api/schedules/share` | guardar una instantánea y obtener id |
| `GET` | `/api/shared/{id}` | recuperar una instantánea compartida |
| `GET` | `/api/schedules/export.ics` | calendario `.ics` de 15 semanas |

Ejemplo de generación:

```bash
curl -X POST http://127.0.0.1:8000/api/schedules/generate \
  -H 'Content-Type: application/json' \
  -d '{"year_id":"year-matematicas-1","blocked":[{"day":"fri","start":"08:00","end":"21:00"}]}'
```

## Modelo de datos

El detalle está en [`docs/data-model.md`](docs/data-model.md). En resumen:

```json
{
  "id": "ses-d3f1ad13c3",
  "subject_id": "sub-matematicas-1-topologia-de-los-espacios-euclideos",
  "group_id": "grp-matematicas-1-topologia-de-los-espacios-euclideos-cle01",
  "type": "expositive",
  "day": "tue",
  "start": "11:00",
  "end": "12:00",
  "duration_minutes": 60,
  "room_id": "room-facultad-de-matematicas-aula-02",
  "building_id": "bld-facultad-de-matematicas",
  "instructors": [],
  "weeks": null
}
```

Frente al CSV original (`Primero;M11;...;Aula 02;11:00-12:00`), cada sesión es
autocontenida y no depende de una posición, de un separador ni de una etiqueta
de UI. Las referencias son ids; los nombres viven en su propia entidad. El
`slot` original se conserva en `legacy.slot` para trazabilidad.

## Decisiones y limitaciones

- **Edificio inferido.** El CSV no incluye edificio. El importador asigna
  *Facultad de Matemáticas* al Grado de Matemáticas y *Facultad de Física* al
  Doble Grado, y lo documenta en `source.notes`. Es un campo editable en el
  catálogo, no una verdad de la fuente.
- **Semana tipo.** Las URLs de `config.yaml` apuntan a la semana 7 de 2026/27.
  El catálogo no inventa semanas lectivas: `weeks` es `null` y el término se
  etiqueta como «semana tipo». El `.ics` exporta 15 repeticiones desde el
  próximo lunes.
- **Optativas.** En 4º de Mates-Física hay materias que comparten franjas. El
  generador devuelve un subconjunto máximo sin solapes y lista las excluidas en
  lugar de producir un horario imposible.
- **Sin cuentas ni profesores.** El enlace compartido es una instantánea, no un
  documento colaborativo. El modelo ya reserva `instructors` por sesión y
  `kind` por aula para futuras extensiones.
- **Datos de ejemplo.** No se ha contrastado cada aula/edificio con la fuente
  oficial. Para producción, conviene importar desde el calendario oficial y
  validar aulas.

## Próximos pasos

1. Importador directo desde la web de la USC (en lugar del CSV).
2. Persistencia en SQLite/Postgres y cuentas de estudiante.
3. Profesores y solicitudes de cambio de clase con aprobación.
4. Huecos comunes entre varios estudiantes de un mismo grupo.
5. Suscripción `.ics` actualizable y avisos de cambios.
