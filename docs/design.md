# Plan actualizado de v1

El objetivo y los cinco comandos del plan original se mantienen. Implementar y validar en este orden:

1. Crear `.venv`, verificar su intérprete y versión, crear `pyproject.toml`, instalar editable + dev.
2. Implementar propiedad de artefactos, bloques, merges estructurados y escrituras atómicas.
3. Detectar proyectos estáticamente; inicializar contexto local sin exigir Serena.
4. Obtener el esquema real de Serena en staging aislado y configurar Codex preservando ajustes.
5. Generar el mapa determinista, con extractores auto, presupuesto e imports centrales.
6. Comprobar staleness estructural, doctor local y conexión MCP separada.
7. Tests/lint/typecheck en `.venv`, wheel y E2E aislado; documentar resultados reales.

## Correcciones incorporadas

- Estimar tokens como `ceil(utf8_bytes / 4)`; presupuesto inicial 1000.
- Memorias lazy, sin placeholders; después de crearlas son propiedad del usuario/agente.
- Categorías explícitas GENERATED, MANAGED_BLOCK, STRUCTURED_MERGE y CREATE_ONCE_USER_OWNED.
- Eliminar el launcher por repositorio. Solo la CLI instalada genera/refresca mapas.
- Extractores específicos reclaman primero; fallback genérico sin duplicación.
- Ranking barato por puntos de entrada, símbolos públicos e indegree de imports internos.
- Estado de obsolescencia exclusivamente en `.ai/.cache/`, siempre ignorado.
- `repo_map.tracked: true` por defecto; las reglas de ignore no cambian el índice de Git.
- `init` completa acciones locales aunque Serena falte; su YAML queda pendiente.
- CI unitario amplio; una combinación de integración real separada.
- Entorno de desarrollo `.venv` y Serena como herramienta externa, sin incluirla en el wheel.

## Supuestos cambiados y diseño obsoleto

Ya no se exige Serena para init. Ya no hay cinco memorias obligatorias ni notas de Playwright automáticas sin evidencia. Ya no existe `.ai/tools/repo_map.py`. Ya no se configura `[python, generic]` por defecto ni se ejecuta MCP real en toda la matriz de CI.

El esquema Serena depende de la instalación verificada, no de una plantilla histórica embebida. Para obtenerlo se ejecuta `serena project create` solo sobre una fixture temporal, con `SERENA_HOME` temporal; se descarta su registro temporal. La v1 reconoce el esquema `language_servers` de Serena 1.7.0. Otros esquemas quedan pendientes con diagnóstico explícito.

## Límites y criterios de aceptación

Contenido manual siempre conservado, incluido al usar force. Memorias no se reescriben. Igual entrada produce iguales bytes; segunda ejecución no altera timestamps. Repo Map incluye su cabecera dentro del presupuesto. Imports relativos Python se resuelven contra módulos internos para contar importadores distintos.

Python usa AST y compara estructura pública sin cuerpos/docstrings. En archivos genéricos se compara contenido de forma conservadora; no se promete distinguir cambios semánticos sin un extractor. Una extracción Python fallida conserva el mapa anterior y nunca se certifica fresh.

Un clon dispone del mapa versionado aunque no de su caché; refresh reconstruye esta última. No se alteran permisos de confianza, autenticación, hooks ni el índice de Git. doctor --connect crea únicamente estado operativo de Serena, con cierre de procesos. No se usan LLM ni MCPs redundantes.

La aceptación requiere tests de preservación, idempotencia, dry-run y staleness, lint/typecheck, wheel limpio, y una integración real con handshake, símbolo y referencia correctos. No se considera una configuración registrada equivalente a conexión verificada.
