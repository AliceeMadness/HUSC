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

[Pantalla de inicio](docs/landing-light.png) · [Matrícula mixta](docs/workspace-mixed.png) · [Selector de matrícula](docs/plan-dialog.png) · [Extraescolares y fijaciones](docs/workspace-extras.png) · [Diálogo extraescolar](docs/event-dialog.png) · [Tema oscuro](docs/workspace-dark.png)

## Funcionalidad

- **Catálogo serializable y versionado** en `data/catalog.json`: grados, cursos,
  asignaturas, grupos, aulas, edificios y sesiones con ids estables.
- **Matrícula personalizada**: mezcla asignaturas de varios cursos y grados. Sirve
  para repetidores (1º + 2º) y para dobles grados (Matemáticas + Física), incluso
  con cursos distintos en cada título.
- **Selector de grupos** de expositiva, laboratorio y seminario con aviso
  instantáneo de solapes y de coincidencia con tus preferencias.
- **Calendario semanal** con bloques coloreados por materia, conflictos marcados
  y geometría a 30 minutos.
- **Bloques consecutivos unidos**: `9:00–10:00` + `10:00–11:00` se muestran como
  una sola clase `9:00–11:00` (también en el `.ics`), conservando las sesiones
  originales para moverlas o exportarlas.
- **Clases extraescolares** recurrentes: título, día, hora, lugar y color. Se
  pintan en el calendario, bloquean huecos, avisan de cruces y el generador las
  evita.
- **Horas fijadas**: un grupo puede marcarse como inamovible. El generador
  conserva sus horas y el modo «mover» no ofrece alternativas para él.
- **Generación automática** con un solver de restricciones: minimiza días y
  huecos, respeta franjas bloqueadas, extraescolares y grupos fijados; si hay
  optativas incompatibles, devuelve el mayor subconjunto que cabe sin solapes.
- **Huecos libres** por día con duración mínima configurable (30 min / 1 h / 1,5 h).
- **Modo «mover una clase»**: al pulsar una clase, busca huecos compatibles para
  el grupo y para el resto de tu horario, y permite copiar la propuesta.
- **Compartir** con un enlace corto (se guarda una instantánea en el servidor,
  incluyendo extraescolares y fijaciones).
- **Exportar** a JSON, calendario `.ics` o PDF mediante impresión.
- **Sincronización real con la USC** usando el mismo acceso AJAX que la app de
  referencia, con fallback sintético en SQLite.
- **Tema claro/oscuro**, diseño responsive y persistencia local del horario.

## Stack

| Capa | Tecnología |
| --- | --- |
| Backend | Python 3.11+ · FastAPI · Pydantic v2 |
| Datos | JSON versionado con JSON Schema + validación referencial |
| Ingesta USC | `httpx` + BeautifulSoup + SQLite (`data/usc_sync.db`) |
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

69 tests cubren integridad del catálogo, importación, detección de solapes,
huecos, generación para los nueve cursos y para matrículas que mezclan
cursos/grados, bloques consecutivos, extraescolares, fijaciones, preferencias,
enlaces compartidos, ICS, parser USC, fallback SQLite y los endpoints
principales. La interfaz obtiene 100/100 en accesibilidad, buenas prácticas y SEO
con Lighthouse, tanto en tema claro como oscuro.

## Estructura

```text
backend/
  main.py          API FastAPI + servido de la SPA
  models.py        modelo Pydantic y validación referencial
  scheduling.py    solver, solapes, huecos, bloques unidos, fijaciones
  store.py         índices del catálogo y persistencia de enlaces
  usc_source.py    acceso AJAX a la USC + fallback sintético + SQLite
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
  usc_sync.db      BBDD SQLite de la última sincronización (generada)
  usc_sync.csv     CSV normalizado de la última sincronización (generado)
  shared_schedules/
scripts/
  import_legacy.py importador CSV/JSON -> catálogo
  sync_usc.py      USC AJAX o fallback sintético -> SQLite/CSV/catálogo
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
| `GET` | `/api/degrees` | grados, cursos, asignaturas y metadatos |
| `GET` | `/api/years/{year_id}` | vista enriquecida de un curso (grupos + sesiones + aulas) |
| `GET` | `/api/plan` | matrícula enriquecida por `year_id` o `subject_ids` |
| `POST` | `/api/schedules/validate` | solapes, grupos que faltan, huecos y estadísticas |
| `POST` | `/api/schedules/generate` | generar una combinación de grupos |
| `POST` | `/api/free-slots` | complemento libre de un conjunto de grupos |
| `POST` | `/api/schedules/share` | guardar una instantánea y obtener id |
| `GET` | `/api/shared/{id}` | recuperar una instantánea compartida |
| `GET` | `/api/schedules/export.ics` | calendario `.ics` de 15 semanas |
| `POST` | `/api/schedules/export.ics` | `.ics` con bloques unidos y extraescolares |
| `GET` | `/api/sync/status` | última sincronización USC (live/synthetic) |

Ejemplo de generación:

```bash
# Curso completo
curl -X POST http://127.0.0.1:8000/api/schedules/generate \
  -H 'Content-Type: application/json' \
  -d '{"year_id":"year-matematicas-1","blocked":[{"day":"fri","start":"08:00","end":"21:00"}]}'

# Matrícula mixta: 1º de Matemáticas + 2º de Matemáticas + 1º de Mates-Física
curl -X POST http://127.0.0.1:8000/api/schedules/generate \
  -H 'Content-Type: application/json' \
  -d '{"subject_ids":[
    "sub-matematicas-1-topologia-de-los-espacios-euclideos",
    "sub-matematicas-2-geometria-lineal",
    "sub-matematicas-fisica-1-fisica-general-ii"
  ]}'
```

## Acceso a los horarios de la USC

La app de referencia no descarga una página normal: hace un **POST AJAX a Drupal**
al widget del calendario, con `_drupal_ajax=1`, `js=true` y el token
`ajax_page_state[libraries]`, y parsea el fragmento HTML
`#course-detail-controller` (`.calendar-day`, `.ml-academic-subject`). Esa
mecánica está reproducida en `backend/usc_source.py`.

```powershell
# Intenta la USC para los 9 cursos de config.yaml; si un curso falla,
# rellena ese curso con datos sintéticos. Guarda la foto en SQLite.
python scripts/sync_usc.py

# Fuerza el fallback inventado (útil sin red o para demos)
python scripts/sync_usc.py --synthetic
```

La sincronización escribe:

- `data/usc_sync.db`: SQLite con `sync_runs` y `sessions` (fuente `usc` o
  `synthetic`), consultable en `GET /api/sync/status`.
- `data/usc_sync.csv`: CSV legacy normalizado.
- `data/catalog.json`: catálogo que consume la API.

El endpoint de la USC puede cambiar su token o su HTML. En ese caso el script
registra el error y genera datos sintéticos para no dejar la aplicación sin
horarios. Después de sincronizar hay que **reiniciar FastAPI** para recargar el
catálogo.

Ejemplo con extraescolar y grupo fijado:

```json
{
  "year_id": "year-matematicas-1",
  "pinned_group_ids": ["grp-matematicas-1-topologia-de-los-espacios-euclideos-cle02"],
  "custom_events": [
    {"id":"evt-1","title":"Trabajo","day":"fri","start":"18:00","end":"20:00","location":"Oficina"}
  ]
}
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
- **Repeticiones y dobles grados.** El horario ya no se limita a un `year_id`:
  es una matrícula de asignaturas. El catálogo de origen aún conserva el doble
  grado como un `degree` separado con las asignaturas de Física del CSV; la UI
  permite añadir las de Matemáticas. Una versión de producción debería incorporar
  las plantillas oficiales de cada plan de estudios.
- **Sin cuentas ni profesores.** El enlace compartido es una instantánea, no un
  documento colaborativo. El modelo ya reserva `instructors` por sesión y
  `kind` por aula para futuras extensiones.
- **Datos incluidos.** El catálogo por defecto es una foto estable de la semana
  tipo para que los tests sean reproducibles. `scripts/sync_usc.py` permite
  refrescarlo desde la USC o generar un fallback sintético en SQLite.
- **Aulas y edificios.** No se ha contrastado cada aula con la fuente oficial;
  el edificio se infiere del grado. La sincronización real mantiene las aulas
  que publica la USC.

## Próximos pasos

1. Sincronización programada (cron) y avisos cuando cambie una clase.
2. Plantillas oficiales de plan de estudios para cada grado y doble grado.
3. Persistencia en SQLite/Postgres y cuentas de estudiante.
4. Profesores y solicitudes de cambio de clase con aprobación.
5. Huecos comunes entre varios estudiantes de un mismo grupo.
6. Suscripción `.ics` actualizable y avisos de cambios.
