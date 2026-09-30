# codex-context

Versión actual: **v0.1.0**.

CLI Python reutilizable para preparar contexto compacto de Codex + Serena. Detecta el proyecto sin importar ni ejecutar su código, conserva el contenido manual y genera un mapa determinista sin LLM.

**Memory → Symbols → References → Targeted Code → Repo Map → Broad Search**

Serena es el único MCP obligatorio. Esta herramienta no añade Filesystem, Memory, Playwright ni Git/GitHub MCP.

## Instalación

Python ≥3.11. Desde este checkout:

```sh
uv tool install .
# Alternativa:
pipx install .
# También se puede instalar el wheel generado:
uv tool install ./dist/codex_context-0.1.0-py3-none-any.whl
```

No se ha publicado este paquete en PyPI. `uv`/`pipx` son herramientas de instalación externas.

## Primera configuración de la máquina

Codex CLI debe estar disponible. Serena se instala en su propio entorno externo:

```sh
uv tool install -p 3.13 serena-agent
codex-context setup-global
# O instalación explícita mediante la herramienta:
codex-context setup-global --install-serena
```

Sin Serena, `setup-global` muestra instrucciones y sale sin modificar la configuración. `--install-serena` requiere `uv`; no instala ni actualiza Serena por defecto. Se verifica la CLI instalada y se utiliza preferentemente `serena setup codex`, primero sobre una configuración temporal. Solo se integra la entrada Serena en la configuración real; otros servidores y opciones se conservan. No se instalan hooks.

La configuración respeta `CODEX_HOME` y `SERENA_HOME`. Una entrada Serena existente incompatible o duplicada requiere resolución manual. Reinicia la sesión de Codex después de configurar el MCP. En la app, activa el repositorio con Serena si el directorio de inicio no coincide con el proyecto.

## Proyecto nuevo

```sh
cd repository
codex-context init .
codex-context doctor .
```

`init` funciona sin Serena: crea el bloque de AGENTS, configuración local, mapa y memorias útiles. La configuración `.serena/project.yml` queda pendiente hasta que una instalación compatible permita obtener su esquema nativo. Ejecuta `setup-global` y luego repite `init` para completarla. No se inventa un YAML de Serena ni se inicializa Git automáticamente.

Memorias lazy: `commands.md` cuando se detectan comandos y `project.md` como entrada a esas notas. Architecture, decisions, pitfalls y notas de navegador/recursos solo aparecen con evidencia durable explícita. Un repositorio vacío puede no necesitar ninguna memoria. No se crean encabezados vacíos, decisiones supuestas ni restricciones de rendimiento inventadas.

La detección inicial incluye Python, Node/TypeScript, Rust, Go y extensiones de otros lenguajes. Los comandos se deducen de manifiestos; no se ejecutan para descubrirlos. Los perfiles Python + Playwright no añaden un MCP de navegador.

## Durante el desarrollo

```sh
codex-context map .
codex-context refresh .
codex-context doctor .
codex-context doctor . --connect
```

`map` recalcula el contenido; `refresh` reutiliza estructura válida y actualiza lo obsoleto. Ninguno reescribe archivos idénticos ni memorias. `doctor` es un diagnóstico local; `--connect` añade handshake MCP y activación real, y puede crear cachés de Serena en el proyecto.

Para Python, cambios de cuerpos, comentarios y docstrings no invalidan el mapa. Imports, firmas públicas, clases, archivos y configuración estructural sí lo invalidan. Para archivos sin extractor semántico, el fallback compara contenido conservadoramente. Tras clonar, el mapa versionado está disponible inmediatamente, pero `refresh` debe reconstruir la caché local para certificar su frescura.

## Configuración del proyecto

`.ai/context.yml`:

```yaml
schema_version: 1
repo_map:
  budget_tokens: 1000
  tracked: true
ignored_paths: []
profile: auto
extractors: auto
```

La estimación es exactamente `ceil(utf8_bytes / 4)`, incluida la cabecera y el aviso de recorte: aproxima tokens, no reproduce la tokenización de Codex. El mínimo configurable es 64 para permitir la cabecera y el resumen. El ranking combina puntos de entrada, símbolos públicos e indegree de imports internos; no utiliza PageRank.

`extractors: auto` da prioridad a extractores específicos; el genérico recibe únicamente archivos no reclamados. También se acepta una lista explícita, por ejemplo `[python, generic]`. Perfiles: `auto`, `base`, `python`, `python-playwright`, `node`. Los patrones adicionales usan sintaxis `.gitignore`.

El inventario utiliza Git tracked + untracked no ignorados, incluidos archivos nuevos antes de `git add`. Sin Git aplica reglas `.gitignore` anidadas. Excluye binarios, dependencias, entornos virtuales, builds, caches, archivos generados habituales y los propios artefactos de contexto; no sigue symlinks.

Versionables: `.ai/context.yml`, el mapa cuando `tracked: true`, AGENTS y configuración/memorias Serena. Toda la caché de obsolescencia vive en `.ai/.cache/`, ignorada por `.ai/.gitignore`. `tracked: false` añade una regla para el mapa; no elimina del índice archivos ya trackeados ni realiza `git add`.

## Propiedad, idempotencia y conflictos

| Categoría | Ejemplos | Semántica |
|---|---|---|
| GENERATED | Repo Map | Regeneración completa solo si contiene una marca y checksum válido de la herramienta. Una edición manual provoca conflicto. |
| MANAGED_BLOCK | AGENTS global/proyecto, `.ai/.gitignore` | Solo cambia el bloque delimitado; conserva texto exterior. Delimitadores ambiguos producen error. |
| STRUCTURED_MERGE | YAML del proyecto, TOML de Codex | Completa claves ausentes; conserva valores manuales, claves desconocidas y comentarios. Serena incorpora lenguajes/exclusiones detectados sin quitar los existentes. |
| CREATE_ONCE_USER_OWNED | Memorias | Se crean únicamente cuando son útiles y no existen; después nunca se sobrescriben. |

En AGENTS se utilizan `<!-- codex-context:start -->` y `<!-- codex-context:end -->`. Si `AGENTS.override.md` oculta esas instrucciones, doctor lo informa. La política global ocupa aproximadamente 86 tokens con el estimador de la herramienta; `initial_prompt` es vacío en configuraciones nuevas de Serena. Un prompt manual existente se conserva y se señala.

```sh
codex-context init . --dry-run
codex-context refresh . --dry-run
codex-context setup-global --dry-run
codex-context map . --force
```

`--dry-run` no escribe archivos ni instala dependencias. `--force` evita la caché de extracción; nunca permite apropiarse de archivos preexistentes, borrar contenido manual ni sobrescribir memorias. Las escrituras son atómicas y se prepara el conjunto de cambios antes de aplicarlo, con recuperación ante errores de escritura. No se modifica código funcional del proyecto.

Códigos de salida: `0` operación correcta (init local puede dejar Serena pendiente), `1` fallo operativo/conflicto, `2` argumentos o configuración inválidos, `3` doctor con acciones pendientes o mapa stale.

## Desarrollo y pruebas

```sh
python -m venv .venv
# Linux/macOS:
source .venv/bin/activate
# Windows PowerShell:
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
python -m pytest -m "not integration"
python -m ruff check src tests
python -m mypy
python -m build --wheel
```

Se puede usar explícitamente `.venv/bin/python` o `.venv\Scripts\python.exe` en lugar de activar el entorno. Todas las dependencias y comandos de desarrollo se ejecutan dentro de `.venv`; Serena no se instala ahí. El wheel incluye solo el paquete de `src`, sin `.venv`, cachés ni herramientas de integración.

`.venv-integration/` es únicamente un área local opcional para E2E: contiene herramientas externas instaladas por uv y sus cachés aisladas. Está ignorada por Git. La CLI no busca ni utiliza ese directorio y la configuración normal de Codex no lo referencia; en las pruebas reales, la instalación de Serena se expone temporalmente mediante PATH y se configura exclusivamente con `CODEX_HOME`/`SERENA_HOME` temporales. `uv` también debe estar disponible en ese PATH de prueba para que Serena inicie su servidor de lenguaje.

Los tests usan homes y repositorios temporales. La integración real exige Codex CLI y Serena externos en PATH y se habilita expresamente:

```sh
# Linux/macOS:
CODEX_CONTEXT_INTEGRATION=1 .venv/bin/python -m pytest -m integration -v
# Windows PowerShell:
$env:CODEX_CONTEXT_INTEGRATION = '1'
.\.venv\Scripts\python.exe -m pytest -m integration -v
```

No necesita login ni un LLM: verifica configuración, handshake, activación, símbolos, referencias y flujo end-to-end. El backend Python predeterminado de Serena usa Pyright; necesita uv y un Node compatible. La prueba local se valida con Node 24 y la CI real usa Node 22. `doctor --connect` también solicita un overview de símbolos cuando hay un archivo de código elegible, para detectar errores de arranque del backend.

CI unitario: Windows/Linux/macOS y Python 3.11/3.14. CI real separado: Ubuntu/Python 3.13, Serena 1.7.0 y Codex 0.159.2, en push a main, releases o ejecución manual.

Para ampliar un stack, añade un detector/perfil en `detection.py`; para navegación estructural añade un extractor registrado en `extractors.py`. Las notas durables pueden apuntar a secciones documentadas de invariantes, decisiones o problemas conocidos para evitar duplicar documentación.

El diseño actualizado y sus límites están en [docs/design.md](docs/design.md).
