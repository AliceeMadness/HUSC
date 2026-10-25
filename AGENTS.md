# AGENTS.md · Guía para agentes de código en HUSC

Este archivo es para cualquier agente (Claude Code, Codex, Copilot…) o persona
que vaya a tocar el código. Resume cómo está montado el proyecto, cómo se
arranca y se prueba, qué reglas de producto ya están decididas y qué trampas
conocidas hay. Léelo entero antes del primer cambio.

> Si algo de aquí contradice al código, manda el código: corrígelo aquí en el
> mismo cambio. El `README.md` es la cara pública del proyecto y a veces va por
> detrás (ver [Documentación desactualizada](#documentación-desactualizada)).

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
  a JSON, `.ics` o PDF (impresión).

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
  `-qq` desaparece la línea de resumen (`107 passed in …`).
- Estado actual: **107 tests** en verde (`test_api.py` 34 · `test_scheduling.py`
  27 · `test_catalog.py` 12 · `test_usc_source.py` 8, algunos parametrizados).
- Los tests usan `TestClient`, no necesitan servidor arrancado ni red.
- Tests que fijan datos reales del catálogo (ids de asignaturas, nº de cursos…)
  se rompen si se resincroniza: actualízalos a conciencia, no los borres.

### Versión de Python

El README dice 3.11+, pero el `.venv` del equipo de desarrollo es **Python 3.10**.
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
  main.py          FastAPI: modelos de petición, endpoints, ICS, servido de la SPA
  models.py        Modelo Pydantic del catálogo + validación referencial
  scheduling.py    Solapes, validación, generación, reparación ("Resolver solapes"), huecos
  store.py         CatalogStore: índices en memoria, vistas enriquecidas, enlaces compartidos
  usc_source.py    Acceso AJAX a la USC, parser HTML, edificios, fallback sintético, SQLite
  timeutil.py      HH:MM <-> minutos, días, semana ISO -> curso académico actual
  ids.py           slugify() y natural_key()
frontend/
  index.html       Toda la SPA: portada (#landing), espacio de trabajo (#workspace), diálogos
  css/styles.css   Tokens de color (claro/oscuro), layout, componentes
  js/app.js        Estado, eventos, render de paneles, semanas, solapes locales, compartir, exportar
  js/timetable.js  Render de la rejilla semanal y layout de clases solapadas
  js/api.js        Cliente fetch (lanza Error con .status)
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
                         app.js: estado, solapes locales instantáneos,
                         validación/reparación en servidor, localStorage
```

- **El servidor es la fuente de verdad** para validar (`/api/schedules/validate`)
  y reparar (`/api/schedules/optimize`).
- **El frontend recalcula solapes en local** (`buildLocalSchedule`,
  `groupConflicts`, `sessionsOverlap`, `sharesWeek`) para que la UI responda
  al instante. La validación del servidor llega con debounce
  (`scheduleValidation` → `refreshValidation`, con `validationToken` para
  descartar respuestas viejas).
- Si cambias la lógica de solape en `scheduling.py`, **cambia también su gemela
  en `app.js`** (y viceversa). Son dos implementaciones de la misma regla:
  - mismo día,
  - intervalos `[start, end)` que se cortan (tocarse en el borde no es solape),
  - mismo cuatrimestre,
  - al menos una semana ISO en común (`weeks = null` significa "todas").

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

- Los modelos de petición viven en `main.py` y tienen límites (`max_length`,
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
- Todo el HTML vive en `index.html`; `app.js` rellena con `innerHTML` usando
  plantillas. **Escapa siempre** datos variables con `escapeHtml()`
  (nombres de asignaturas, títulos de extraescolares, lugares…).
- Las referencias al DOM se recogen una vez en `els` dentro de `init()`. Un
  elemento nuevo con `id` → añádelo a `els` y enlaza eventos en `bindEvents()`.
- El estado global está en `state` (arriba de `app.js`). Tras cambiar la
  selección llama a `onSelectionChanged()` / `renderEverything()` y a
  `saveLast()` según corresponda; mira cómo lo hacen las funciones vecinas.
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
- **Edificio en cada clase**: etiqueta `<<Edificio>> 📍` con el nombre corto
  (Matemáticas, Física, ETSE, Emprendia, Condesa).
- **Tema claro/oscuro**: `applyTheme`, guardado en `horario:theme`.

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
- La paleta de asignaturas es `PALETTE` en `app.js`.
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
  aunque las publique la ETSE. "LB 5" se queda en la ETSE. Condesa no tiene
  aulas en los datos actuales.
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

- Rama de trabajo de Alejandro: **`rama-alejandro`**. La principal es `main`.
  Remoto: `https://github.com/Piqueiras/HackETSE.git`.
- **Haz commit o push solo cuando el usuario lo pida.** No hagas force-push,
  no reescribas historia y no fusiones en `main` sin permiso explícito.
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

## 13. Documentación desactualizada

Al escribir este archivo, el `README.md` tenía estos desfases:

- dice "90 tests"; hay 107;
- dice "Python 3.11+"; el entorno real es 3.10 y el código lo soporta;
- la descripción de la app en `main.py` (`FastAPI(description=…)`) solo menciona
  Matemáticas y Matemáticas + Física;
- en "Decisiones y limitaciones" aún habla de "sincronización real de la semana
  43", aunque ya se sincroniza semana a semana;
- el ejemplo de `/api/schedules/generate` usa `blocked`, que la UI ya no envía.

Corrígelos cuando toques esas secciones.

## 14. Ideas pendientes (no empezar sin que se pida)

- Publicar la web solo con GitHub Pages + GitHub Actions. Implica:
  - portar a JS la validación y "Resolver solapes";
  - servir el catálogo como JSON estático;
  - meter los horarios compartidos en la URL.

  Alternativa: un hosting con backend (Render, Railway…) conectado al repo.
- Sincronización programada y avisos de cambios de clase.
- Huecos comunes entre varios estudiantes.
- Cuentas, persistencia en base de datos y suscripción `.ics` actualizable.
