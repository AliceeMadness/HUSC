# AGENTS.md · Guía para agentes de código en HUSC

Este archivo es para cualquier agente (Claude Code, Codex, Copilot…) o persona
que vaya a tocar el código. Resume cómo está montado el proyecto, cómo se
arranca y se prueba, qué reglas de producto ya están decididas y qué trampas
conocidas hay. Léelo entero antes del primer cambio.

> Si algo de aquí contradice al código, manda el código: corrígelo aquí en el
> mismo cambio. El `README.md` es la cara pública del proyecto y a veces va por
> detrás: revísalo cuando cambies comportamiento (ver
> [Problemas conocidos](#13-problemas-conocidos-sin-arreglar)).

---

## 1. Qué es HUSC

**HUSC** es un constructor de horarios para los grados de la Universidade de
Santiago de Compostela (USC). El estudiante (o profesor) elige asignaturas y
grupos (expositiva, laboratorio, seminario) y la app:

- detecta solapes **semana a semana** (dos clases solo chocan si comparten
  semana ISO, día y franja);
- muestra cada semana del curso, agrupando las que tienen el mismo horario;
- resuelve solapes cambiando el menor número de grupos posible;
- permite fijar grupos, añadir extraescolares, compartir por enlace y exportar
  a JSON, `.ics` o PDF (descarga directa generada en el navegador; el botón
  de la impresora sigue abriendo la impresión).

Grados incluidos: Matemáticas, Física, Ingeniería Informática, Inteligencia
Artificial y los dobles grados Informática + Matemáticas y Matemáticas + Física.
Los datos son del curso **2025/26**, sincronizados semana a semana desde el
widget AJAX de la web de la USC.

Es un prototipo de hackathon (HackETSE). Prima que funcione y se vea bien sobre
la arquitectura "enterprise", pero el código está cuidado: mantén ese nivel.

## 2. Arranque rápido

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
uvicorn backend.main:app --reload --port 8000
```

- App: <http://127.0.0.1:8000> · API interactiva: <http://127.0.0.1:8000/docs>
- No hay `npm`, ni bundler, ni build step. FastAPI sirve `frontend/` tal cual.
- Tras cambiar `data/catalog.json` (p. ej. al sincronizar) **reinicia uvicorn**:
  el catálogo se carga una vez en memoria (`store = CatalogStore()`).
- El middleware `revalidate_frontend` pone `Cache-Control: no-cache` a todo lo
  que no es `/api`, así que un recargado normal del navegador ya ve los
  cambios de HTML/CSS/JS.

### Tests

```powershell
.\.venv\Scripts\python.exe -m pytest -p no:warnings
```

- `pyproject.toml` ya tiene `addopts = "-q"`. **No añadas otro `-q`**: con
  `-qq` desaparece la línea de resumen (`113 passed in …`).
- Estado actual: **113 tests** en verde (`test_api.py` 40 · `test_scheduling.py`
  53 · `test_catalog.py` 12 · `test_usc_source.py` 8, algunos parametrizados).
- Los tests usan `TestClient`, no necesitan servidor arrancado ni red.
- Tests que fijan datos reales del catálogo (ids de asignaturas, nº de cursos…)
  se rompen si se resincroniza: actualízalos a conciencia, no los borres.

### Versión de Python

El `.venv` del equipo de desarrollo es **Python 3.10** (el README dice 3.10+).
El código debe seguir funcionando en 3.10:

- `date.fromisoformat("2025-W43-1")` **no** funciona en 3.10 → usa
  `date.fromisocalendar(year, week, 1)` (ya se hace en `timeutil.py` y
  `usc_source.py`).
- Nada de `match` con patrones raros, `tomllib`, `ExceptionGroup`, etc.
- `from __future__ import annotations` está en todos los módulos: puedes usar
  `list[str] | None` en anotaciones.

## 3. Estructura del repositorio

```text
backend/
  main.py          FastAPI: endpoints (HTTP -> funciones), middleware, servido de la SPA
  schemas.py       Modelos de las peticiones (con límites)
  ics.py           Exportación a calendario .ics
  models.py        Modelo Pydantic del catálogo + validación referencial
  scheduling/      Algoritmos (``from backend.scheduling import …`` reexporta todo):
    common.py        normalización, búsqueda de grupos, regla de solape (_overlaps)
    views.py         solapes, huecos y bloques unidos sobre vistas de sesión
    validation.py    validate_selection, free_slots_for_groups
    variables.py     modelo de búsqueda común (Variable, _quality, fijaciones)
    generator.py     generate_schedule (solo API)
    repair.py        optimize_schedule ("Resolver solapes")
  store.py         CatalogStore: índices en memoria, vistas enriquecidas, enlaces compartidos
  usc_source.py    Acceso AJAX a la USC, parser HTML, edificios, fallback sintético, SQLite
  timeutil.py      HH:MM <-> minutos, días, semana ISO -> curso académico actual
  ids.py           slugify() y natural_key()
frontend/
  index.html       Toda la SPA: portada (#landing), espacio de trabajo (#workspace), diálogos
  css/styles.css   Tokens de color (claro/oscuro), layout, componentes
  js/              ES modules sin build; ``app.js`` es la entrada:
    app.js           arranque (init, URL) y conexión de eventos (bindEvents)
    constants.js     paleta, órdenes, etiquetas, claves de localStorage
    state.js         ``state`` (estado único) y ``els`` (elementos del DOM)
    utils.js / ui.js formato y textos / toasts, portapapeles, descargas
    api.js           cliente fetch (lanza Error con .status)
    catalog.js       consultas al directorio de grados
    storage.js       horario guardado y tema en localStorage
    selection.js     índices del plan, grupos elegidos y fijados
    weeks.js         semanas ISO, fecha del dispositivo, selector de semana
    schedule.js      bloques unidos, solapes locales, "Próximamente" (gemela de la regla)
    validation.js    onSelectionChanged y validación del servidor con debounce
    render.js        dibujo del espacio de trabajo (panel, calendario, avisos)
    timetable.js     rejilla semanal y layout de clases solapadas
    events.js        extraescolares
    header.js        tema y cuatrimestre
    landing.js       portada y buscador de grados
    plan.js          abrir la matrícula, cabecera y URL
    plan-dialog.js   diálogo "Editar matrícula"
    resolve.js       "Resolver solapes", caja verde, revertir, fijar grupos
    share.js         compartir y exportar (JSON, .ics, PDF)
    pdf.js           escritor de PDF vectorial mínimo (Helvetica, WinAnsi), sin dependencias
    pdf-export.js    dibuja la semana en pantalla como PDF A4 apaisado
data/
  catalog.json     Catálogo canónico (≈4,7 MB, versionado en git)
  shared_schedules/  Instantáneas de "Compartir" (*.json ignorados por git)
  usc_sync.db / usc_sync.csv  Última sincronización (ignorados por git)
scripts/
  sync_usc.py      USC -> SQLite/CSV/catalog.json (fallback sintético)
  import_legacy.py CSV/JSON antiguo -> catálogo (solo para reproducir el snapshot viejo)
schema/catalog.schema.json   JSON Schema del catálogo
docs/              data-model.md + capturas usadas en el README
tests/             pytest
config.yaml        Fuentes USC por grado/curso, semanas por cuatrimestre, edificios
horario.csv, forms_data.json  Datos legacy de la primera versión
```

## 4. Arquitectura y flujo de datos

```text
USC (Drupal AJAX) ──sync_usc.py──▶ data/catalog.json ──CatalogStore──▶ FastAPI /api/*
                                                                         │
                         navegador ◀── fetch (api.js) ──────────────────┘
                         js/: estado, solapes locales instantáneos,
                         validación/reparación en servidor, localStorage
```

- **El servidor es la fuente de verdad** para validar (`/api/schedules/validate`)
  y reparar (`/api/schedules/optimize`).
- **El frontend recalcula solapes en local** (`buildLocalSchedule`,
  `groupConflicts`, `sessionsOverlap`, `sharesWeek`) para que la UI responda
  al instante. La validación del servidor llega con debounce
  (`scheduleValidation` → `refreshValidation`, con `validationToken` para
  descartar respuestas viejas).
- Si cambias la lógica de solape en `scheduling/common.py` y `scheduling/views.py`,
  **cambia también su gemela en `frontend/js/schedule.js`** (y viceversa). Son dos implementaciones de la misma regla:
  - mismo día,
  - intervalos `[start, end)` que se cortan (tocarse en el borde no es solape),
  - mismo cuatrimestre,
  - al menos una semana ISO en común (`weeks = null` significa "todas").
  Lo mismo vale para clases contra extraescolares (`CustomEvent.weeks`).

## 5. Modelo de datos (`data/catalog.json`)

Detalle completo en [`docs/data-model.md`](docs/data-model.md). Lo esencial:

- Catálogo **plano**: `campuses`, `buildings`, `rooms`, `degrees`, `years`,
  `subjects`, `groups`, `class_sessions`. Todas las relaciones son por id.
- `Catalog._referential_integrity` (en `models.py`) valida al cargar: ids
  únicos, referencias existentes, que el aula sea del edificio, que las semanas
  existan en el calendario y sean del cuatrimestre correcto, y que las listas
  inversas (`subject_ids`, `group_ids`, `session_ids`) coincidan. Si rompes algo
  de esto, la app **no arranca**.
- `ClassSession`:
  - `day` ∈ `mon…sun`, `start`/`end` en `HH:MM` local;
  - `duration_minutes` debe cuadrar con `start`/`end`;
  - `semester` 1|2, copiado de la asignatura;
  - `weeks`: lista de semanas ISO `"2025-W43"` o `null` (= todas);
  - `legacy.slot` conserva el slot del CSV original para trazabilidad.
- `GroupType`: `expositive | laboratory | seminar | other`. En la UI son
  Expositiva / Laboratorio / Seminario / Otro, en ese orden (`GROUP_TYPE_ORDER`).

### Ids

Estables, legibles y generados con `slugify()` (ASCII, minúsculas, guiones):

| Entidad | Ejemplo |
| --- | --- |
| Curso | `year-matematicas-3` |
| Asignatura | `sub-matematicas-1-elementos-de-probabilidad-y-estadistica` |
| Grupo | `grp-matematicas-1-elementos-de-probabilidad-y-estadistica-cle02` |
| Sesión | `ses-01230dadfe` (hash) |
| Aula | `room-facultad-de-matematicas-aula-06` |
| Edificio | `bld-facultad-de-matematicas` |

Los ids viajan en URLs, `localStorage` y enlaces compartidos: **cambiarlos
rompe horarios guardados**. Si el catálogo cambia y un id guardado ya no existe,
la API responde 422 y el frontend borra `horario:last-v2` y avisa con un toast.

Ordena códigos de grupo con `natural_key()` (`CLE02` antes que `CLE10`).

### Semanas y curso actual

- Las semanas se guardan como semana ISO del curso de los datos (2025/26).
- Para "hoy", el `.ics` y la tarjeta "Próximamente" se trasladan al curso
  académico en vigor **conservando el número de semana ISO**
  (`week_in_current_year` en Python, `weekInCurrentYear` en JS): la 2025-W43 se
  trata como la semana 43 del curso actual.
- El curso académico empieza en septiembre (`academic_year_start`).
- Cuatrimestre automático (`semesterForToday`): el 2C empieza en su primera
  semana lectiva según el catálogo; enero cuenta como 1C. El botón
  "1º/2º Cuatrimestre" lo cambia solo para la visita actual (no se persiste).

## 6. API

| Método | Ruta | Uso en la UI |
| --- | --- | --- |
| `GET` | `/api/health` | sí (contadores, metadatos) |
| `GET` | `/api/degrees` | sí (portada y "Editar matrícula") |
| `GET` | `/api/plan?year_id=…` / `?subject_ids=a,b,c` | sí (abrir horario) |
| `GET` | `/api/years/{year_id}` | no (cliente API) |
| `POST` | `/api/schedules/validate` | sí |
| `POST` | `/api/schedules/optimize` | sí ("Resolver solapes") |
| `POST` | `/api/schedules/generate` | no (genera desde cero; queda en API) |
| `POST` | `/api/free-slots` | no (el panel de huecos se quitó; queda en API) |
| `POST` | `/api/schedules/share` / `GET /api/shared/{id}` | sí |
| `GET`/`POST` | `/api/schedules/export.ics` | sí (POST incluye extraescolares y bloques unidos) |
| `GET` | `/api/catalog`, `/api/catalog/export`, `/api/schema/catalog`, `/api/sync/status` | utilidades |

Convenciones:

- Los modelos de petición viven en `schemas.py` y tienen límites (`max_length`,
  `ge`/`le`). Mantén límites en cualquier campo nuevo.
- `ValueError` → 422 y `KeyError` → 404, con el mensaje **en español** en
  `detail`. `api.js` muestra ese `detail` al usuario, así que escribe mensajes
  que entienda un estudiante.
- `optimize_schedule` **repara**, no regenera: solo cambia grupos de las
  asignaturas y tipos que el usuario ya tiene elegidos; los grupos fijados
  (`pinned_group_ids`) nunca se tocan; minimiza el número de cambios.
- Enlaces compartidos: id `secrets.token_hex(4)`, guardados como JSON en
  `data/shared_schedules/`. Son instantáneas, no documentos colaborativos.

## 7. Frontend

### Reglas

- **Vanilla JS con ES modules**, sin dependencias ni build. No metas
  frameworks, CDNs ni `npm` sin que el usuario lo pida.
- Todo el HTML vive en `index.html`; los módulos de `js/` lo rellenan con `innerHTML` usando
  plantillas. **Escapa siempre** datos variables con `escapeHtml()`
  (nombres de asignaturas, títulos de extraescolares, lugares…).
- Las referencias al DOM se recogen una vez en `els` (`collectElements()` en
  `state.js`, llamado desde `init()`). Un elemento nuevo con `id` → añádelo a
  `ELEMENT_IDS` en `state.js` y enlaza sus eventos en `bindEvents()` de `app.js`
  (o en el `bind…()` del módulo, como `bindPlanDialog()`).
- El estado global es `state` en `state.js`. Tras cambiar la selección llama a
  `onSelectionChanged()` (`validation.js`) / `renderEverything()` (`render.js`)
  y a `saveLast()` (`storage.js`) según corresponda; mira cómo lo hacen las
  funciones vecinas.
- Cada módulo tiene una sola responsabilidad (ver la lista de la sección 3).
  Los módulos se importan entre sí en ciclo (p. ej. `render.js` ↔ `resolve.js`):
  es válido en ES modules porque solo se llaman funciones, nunca en la carga.
  No ejecutes código del módulo al importarlo, salvo constantes.
- Comentarios de código **en inglés**; textos de la interfaz **en español**
  (de España: "Cuatrimestre", "Matrícula", "Extraescolar").

### Pantallas y navegación

- `#landing` (portada HUSC) se muestra siempre al abrir la app, salvo que la URL
  apunte a algo concreto:
  - `?s=<id>`: horario compartido;
  - `?plan=a,b,c`: matrícula por asignaturas;
  - `?year=<year_id>`: curso completo.
- `#workspace` es "Mi horario". `showLanding()` / `showWorkspace()` alternan
  entre ambas y ajustan los botones de la cabecera (chip del curso, compartir,
  cuatrimestre).
- **Reglas de producto decididas por el usuario (no las cambies sin que lo pida):**
  - "Volver al Inicio" (`goHome`) **no borra nada**. Solo vuelve a la portada.
  - El botón grande de la portada (`#configure-plan`, `configurePlan`) dice
    **"Ir a mi horario"** si hay un horario en memoria o guardado, y lo
    reabre. Si no lo hay, dice **"Configurar mi horario"** y abre "Editar
    matrícula" vacío. `renderConfigureButton()` actualiza el texto.
  - **"Nueva matrícula"** (`#custom-plan`, en "¿Qué estudias?") es lo **único**
    que borra todo con `resetPlan()`: asignaturas, grupos, fijaciones,
    extraescolares y `localStorage`. Después abre "Editar matrícula" vacío.
  - El título del espacio de trabajo es siempre **"Mi horario"**; el curso y el
    grado van en el subtítulo.
  - "Resolver solapes" vive **dentro de la caja roja de solapes**
    (`renderAlerts` / `resolveButton`), no en la barra de herramientas.
  - Tras resolver queda una **caja verde** (`resolvedAlert`, `.alert-success`; el usuario no la quiere roja) con lo cambiado, un
    botón **"Revertir cambios"** (`revertResolve`, también Ctrl+Z / Cmd+Z fuera
    de campos de texto) y una **cruz a la derecha** (`dismissResolve`) que la
    cierra sin revertir. La instantánea vive en `state.lastResolve` y se descarta
    al revertir, al cerrar, con cualquier cambio manual de grupos o al cambiar
    de matrícula.
  - Un clic en una clase del calendario **fija o desfija** su grupo
    (`togglePin`, icono ⚑).
  - Se pueden elegir **varios grupos del mismo tipo** en una asignatura
    (pensado para profesores).
  - "Editar matrícula" no lista los dobles grados como grados propios y empieza
    con la sección "Tus asignaturas".
- **Cosas que el usuario quitó a propósito; no las reintroduzcas:**
  - mapa de edificios;
  - presets de extraescolares;
  - la portada "con pinta de IA": etiqueta en mayúsculas sobre el título,
    tarjeta de calendario decorativa inclinada, notitas flotantes, degradados
    de fondo, círculos decorativos en las tarjetas e iconos en cuadraditos. La
    portada es sobria: título "HUSC", texto, botón y debajo, en su propia
    línea y al mismo tamaño que el texto, las cifras del catálogo;
  - panel de horas libres / huecos;
  - preferencias de franjas bloqueadas (`blockedSlots()` devuelve `[]` y el
    código asociado queda inerte).

### Piezas de la UI

- **Buscador de grados** (`#degree-search`, en `renderLanding`): normaliza
  quitando tildes y mayúsculas (`normalizeSearch`). Cada palabra de la búsqueda
  debe ser prefijo de alguna palabra de `name` + `short_name`; si nada coincide,
  muestra `.degree-empty`.
- **Selector de semana** (`renderWeekSelect`, `weekPatterns`,
  `weekPatternLabel`): agrupa semanas idénticas, llama "Horario habitual" a la
  más repetida, nombra las demás ("Primera semana", "Semana del 12 oct · 1 clase
  más"…). Mientras `state.weekAuto` sea `true`, sigue la fecha del dispositivo.
  **Solo se avisan los solapes de la semana que se está viendo**: el usuario
  pidió quitar el aviso de "solapes en otras semanas" (no lo reintroduzcas).
- **Bloques unidos**: `mergeLocalSessions` (JS) y `merge_session_views`
  (Python) unen clases consecutivas del mismo grupo y aula (9–10 + 10–11 →
  9–11), conservando las sesiones originales.
- **"Próximamente"** (`#upcoming`, `renderUpcoming` / `upcomingClasses`):
  tarjeta con la clase que empieza en la próxima hora. Se refresca cada 30 s.
- **Extraescolares** (`events.js`): por defecto son de "Todas las semanas"
  (`weeks: null`); con "Solo algunas" se eligen semanas ISO del catálogo en el
  diálogo, agrupadas por cuatrimestre. Solo aparecen y solo cuentan como cruce
  (en la pantalla, en la validación, en «Resolver solapes» y en el `.ics`) en
  sus semanas. Las semanas que solo se diferencian en extraescolares tienen su
  propia entrada en el selector de semana.
- **Cada clase del calendario** muestra el grupo y su tipo ("CLE01 ·
  Expositiva") bajo el nombre, y en la última línea el aula a la izquierda y la
  etiqueta `<<Edificio>> 📍` a la derecha, con el nombre corto (Matemáticas,
  Física, ETSE, Emprendia, Condesa).
- **PDF** (`downloadPdf` en `share.js` → `timetablePdf` en `pdf-export.js` →
  `PdfDocument` en `pdf.js`): dibuja en vectorial la semana que se ve (la misma
  `layoutDay` de `timetable.js`), A4 apaisado, sin dependencias ni servidor.
  Usa Helvetica con WinAnsiEncoding: tildes y ñ salen bien; los emojis (📍) se
  omiten. Para probarlo en headless, captura el blob parcheando
  `URL.createObjectURL` (la descarga por CDP no se guarda) y renderízalo con
  PyMuPDF en un venv aparte con ruta corta (rutas largas rompen `pip`).
- **Tema claro/oscuro**: `applyTheme`, guardado en `horario:theme`. El botón
  (`#theme-toggle`) se ve siempre, también en la portada.

### Persistencia en el navegador

| Clave `localStorage` | Contenido |
| --- | --- |
| `horario:last-v2` | último horario: `planSubjectIds`, `yearId`, `groupIds`, `customEvents`, `pinnedGroupIds`, `autoResult`, `savedAt` |
| `horario:theme` | `light` / `dark` |

- `readSaved()` valida y sanea lo leído. Envuelve todo acceso a `localStorage`
  en `try/catch`, porque puede fallar en modo privado.
- Si cambias el formato guardado de forma incompatible, sube la versión de la
  clave (`-v3`) en vez de romper lo que ya tienen los usuarios.

### Estilos

- Colores USC: azul `#000066` y `#004c93`; en tema oscuro, acento `#7fb3ec`. Ya
  no se usa el verde de las primeras versiones.
- Usa los tokens CSS existentes (`var(--…)` en `:root` y el bloque del tema
  oscuro) en vez de colores sueltos, y comprueba **ambos temas**.
- La paleta de asignaturas es `PALETTE` en `js/constants.js`.
- La interfaz tenía 100/100 en accesibilidad en Lighthouse. Mantén `aria-*`,
  `label`s, foco visible y contraste.
- Comprueba también el ancho de móvil: el diseño es responsive.

## 8. Sincronización con la USC

```powershell
python scripts/sync_usc.py                 # todas las semanas, todos los cursos
python scripts/sync_usc.py --single-week   # solo la semana tipo de cada cuatrimestre
python scripts/sync_usc.py --synthetic     # sin red: datos inventados
python scripts/sync_usc.py --timeout 60
```

- Mecánica: POST AJAX a Drupal
  (`course-detail-controller-week-filter/.../<año>/<semana>?_wrapper_format=drupal_ajax`)
  con `_drupal_ajax=1`, `js=true` y `ajax_page_state[libraries]`. Se parsea
  `#course-detail-controller` (`.calendar-day`, `.ml-academic-subject`) en
  `parse_calendar_html`.
- `config.yaml` define:
  - las fuentes por grado y curso (los dobles grados tienen dos URLs por curso,
    una por facultad, cada una con su `building`);
  - el rango de semanas por cuatrimestre (`first_week` / `last_week`, formato
    `"25/36"`);
  - los edificios.
- Las semanas sin clases (festivos) devuelven respuestas vacías. Se aceptan con
  `allow_empty` y se ignoran; **no las conviertas en error**.
- **Edificios**: el edificio sale de la fuente, salvo que el aula encaje con
  un patrón `rooms` (regex) en `config.yaml`. Las aulas `IA.*` van a Emprendia
  aunque las publique la ETSE. "LB 5" se queda en la ETSE. Las aulas B, C y D
  de Física y los laboratorios de instrumentación van a Condesa (estos
  laboratorios aún no aparecen en los datos de la USC).
- Si una fuente falla, ese curso se rellena con datos sintéticos y queda
  registrado en `data/usc_sync.db` (`GET /api/sync/status`).
- Un `--synthetic` o una sincronización parcial **reescriben
  `data/catalog.json`**. No lo hagas si no te lo piden: el catálogo versionado
  es el real.
- Hecho conocido: Técnicas Experimentales I–III no aparecen en el widget de la
  USC, y la IV solo en las semanas 2026-W05 y W06. Es un hueco de la fuente, no
  un bug del parser. El usuario pidió **no** añadir datos a mano por esto.

## 9. Verificar cambios de UI en un navegador

Los tests no cubren el frontend. Para cambios visibles:

1. Arranca uvicorn (o reutiliza el que ya esté en el puerto 8000).
2. Comprueba en un navegador real, o en headless por CDP:
   - Edge está en
     `C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe`.
   - Lánzalo con `--headless=new --remote-debugging-port=9333
     --remote-allow-origins=http://127.0.0.1:9333 --user-data-dir=<perfil temporal>`
     y habla con él por websocket (`websocket-client` en un venv aparte, **no**
     en el `.venv` del proyecto).
   - Para simular otra fecha (cuatrimestre, "Próximamente", semana actual),
     inyecta un `Date` falso con `Page.addScriptToEvaluateOnNewDocument`.
   - Si imprimes emojis o tildes por consola en Windows, usa
     `PYTHONIOENCODING=utf-8`.
   - El Edge de este equipo tiene la extensión **Dark Reader**: en tema claro
     oscurece la página (fondo `rgb(24, 26, 27)`) y falsea capturas y colores
     calculados. No es un fallo de la app.
3. Limpia `localStorage` al empezar cada prueba, porque el horario guardado
   cambia el comportamiento de la portada.
4. Guarda capturas y scripts de prueba fuera del repo (carpeta temporal), salvo
   las de `docs/` que use el README.

## 10. Trampas conocidas (Windows)

- **uvicorn `--reload` se cuelga** a veces tras muchos cambios. Mata el árbol
  de procesos (`Get-Process python | Stop-Process` o el PID concreto) y vuelve
  a arrancar. No lances un segundo servidor en el mismo puerto.
- **Finales de línea**: git avisa de `LF will be replaced by CRLF`. Es
  inofensivo; no conviertas archivos enteros de formato.
- **Editar con `sed` o heredocs** textos con tildes, comillas o `${}` de JS es
  frágil. Para cambios grandes usa la herramienta de edición o un script Python
  con `read_text`/`write_text(encoding="utf-8")` y comprobación de que cada
  reemplazo ocurre exactamente una vez.
- Lee y escribe siempre con **UTF-8** explícito (`encoding="utf-8"`).
- PowerShell 5.1 no tiene `&&`. Usa `;` o `if ($?) { … }`. El Bash del entorno
  es Git Bash.

## 11. Git y flujo de trabajo

- Repositorio de trabajo: **`https://github.com/AliceeMadness/HUSC.git`**
  (remoto `origin`), rama **`main`**. Render publica la web desde esa rama con
  Auto-Deploy: **cada push a `main` sale a producción**.
- El repositorio original del hackathon sigue como remoto `piqueiras`
  (`https://github.com/Piqueiras/HackETSE.git`, ramas `rama-alejandro`,
  `rama-piqueras`…). Úsalo solo para traer cambios del equipo cuando se pida.
- **Haz commit o push solo cuando el usuario lo pida.** No hagas force-push y
  no reescribas historia. Para cambios grandes trabaja en una rama
  (p. ej. `refactor-estructura`) y fusiónala en `main` cuando el usuario pida
  subirlo.
- Mensajes de commit **en español**, en imperativo o descriptivos, en la línea
  de los existentes ("Resolver solapes en la caja de solapes y fijar grupos con
  un clic"). Si el commit agrupa varias cosas, añade un cuerpo con viñetas.
- No subas archivos generados en ejecución: `data/shared_schedules/*.json`,
  `data/usc_sync.db`, `data/usc_sync.csv`, `__pycache__/`, `.venv/` (ya están
  en `.gitignore`).
- `data/catalog.json` **sí** se versiona. Si lo regeneras, dilo en el commit.

## 12. Checklist antes de dar algo por terminado

- [ ] `python -m pytest -p no:warnings` en verde (y tests nuevos si tocas backend).
- [ ] Si tocaste la regla de solapes: Python y JS siguen iguales.
- [ ] Probado en el navegador: portada → horario → volver, tema claro y oscuro,
      ancho de móvil.
- [ ] Sin errores en la consola del navegador.
- [ ] Textos de la UI en español y datos escapados con `escapeHtml()`.
- [ ] `README.md` y este `AGENTS.md` actualizados si cambia el comportamiento.
- [ ] No reintroducido nada de la lista de cosas quitadas a propósito.
- [ ] Nada subido a git sin que el usuario lo pidiera.

## 13. Problemas conocidos sin arreglar

- **Enlaces compartidos en Render**: se guardan en `data/shared_schedules/`, pero
  el disco del plan gratuito de Render se borra en cada despliegue o reinicio,
  así que los enlaces viejos acaban dando 404. Solución prevista: meter el
  horario en la propia URL (ver sección 14).
- **Desbordamiento en móvil**: con 390 px de ancho la página mide ~738 px por el
  ancho mínimo de `.timetable-panel`; los diálogos salen descentrados. Ya
  pasaba antes del refactor.
- **Paridad JS/Python**: el mismo grupo en dos aulas a la vez cuenta como solape
  en `schedule.js` pero no en `scheduling/` (Python ya lo ignora).
- El `README.md` estaba al día al cerrar este archivo (113 tests, Python 3.10+,
  sincronización semana a semana); si cambias el comportamiento, revísalo.

## 14. Ideas pendientes (no empezar sin que se pida)

- **Dejar Render y publicar solo con GitHub Pages + GitHub Actions** (el usuario
  lo está considerando; Pages gratis exige repo público). Plan acordado:
  - un Action ejecuta Python al publicar y genera JSON estáticos
    (`degrees`, un archivo por curso para no bajar los 4,7 MB enteros);
  - la validación pasa a ser solo la de `schedule.js` (ya casi completa);
  - portar a JS "Resolver solapes" (`scheduling/repair.py`): es lo más costoso;
  - `.ics` generado en JS (como el PDF);
  - compartir metiendo el horario en la URL (arregla además la sección 13).
- **Cuentas** con el horario guardado: factible, dificultad media. Lo guardado
  sería el mismo objeto que `horario:last-v2`. Hace falta una base de datos
  externa (Supabase/Neon; el disco de Render no persiste y su Postgres gratis
  caduca) y, mejor que contraseñas propias, inicio de sesión con Google o enlace
  mágico (Supabase Auth). Alternativa ligera: código de sincronización privado.
- Otras propuestas hechas al usuario: app instalable (PWA), suscripción `.ics`
  que se actualiza sola, resumen de carga semanal, comparar horarios con amigos
  (huecos comunes), sincronización programada con avisos de cambios de clase.
- Pendiente de respuesta del usuario: qué laboratorios de Informática/IA están
  en Condesa (se añadiría un patrón `rooms` en `config.yaml`).
