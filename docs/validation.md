# Resultado de validación local

Implementación y validación realizadas en Windows, con todas las herramientas de desarrollo ejecutadas mediante `.venv/Scripts/python.exe` (Python 3.14.7). El proyecto está instalado editable con `.[dev]`.

## Comprobaciones superadas

- Tests unitarios/funcionales: **40 passed, 1 skipped**. La única omisión es la creación de symlinks, que requiere permisos no disponibles en esta sesión de Windows.
- Integración real: **1 passed**, con Codex CLI 0.159.2, Serena 1.7.0 externa, Python 3.13.15 para Serena y Node 24.19.0.
- MCP: handshake, listado de herramientas, activación de proyecto, búsqueda de `Widget`, referencia desde otro módulo y doctor --connect con overview de símbolos.
- Idempotencia real: dos init mantienen contenido y timestamps; cuerpo Python mantiene fresh y cambio de firma produce stale; refresh devuelve fresh.
- Ruff y mypy strict: correctos, 16 módulos fuente verificados por mypy.
- pip check: sin incompatibilidades.
- Wheel construido e inspeccionado: 20 archivos de paquete/metadatos; sin entornos, cachés ni referencias a `.venv-integration` en el código empaquetado.
- E2E del wheel instalado en un entorno temporal independiente: repositorios vacío, Python y Python + Playwright, con rutas que contienen espacios. init, init --force, doctor y refresh funcionan sin Serena ni el directorio de integración en PATH, preservando AGENTS manual.

Wheel: `dist/codex_context-0.1.0-py3-none-any.whl`.

SHA256: `7b18546f319a7db3a92673f254c18dcec10adcf06aad4e2f6629941ae04dba9a`.

## Separación de entornos

`.venv-integration/` contiene únicamente herramientas externas y cachés para la integración local. Está excluido de Git y no es necesario para ejecutar la CLI. Serena no se ha instalado como dependencia dentro de `.venv`.

Las pruebas reales usan HOME, USERPROFILE, CODEX_HOME y SERENA_HOME temporales. La configuración real de Codex conserva su entrada `node_repl`, sin Serena ni rutas al entorno de integración. La operación normal de setup-global administra la instalación externa detectada o explícitamente instalada mediante uv; no contiene rutas fijas de este checkout.

Se corrigió un problema de CRLF en el staging TOML de Windows. También se identificaron dos requisitos del backend externo: uv debe estar en PATH y el Node seleccionado debe ser compatible con Pyright. Las pruebas se ejecutaron con un PATH temporal adecuado, sin modificar el Node ni la configuración del usuario.

## CI preparada

Matriz funcional: Windows, Ubuntu y macOS; Python 3.11 y 3.14. Integración real separada: Ubuntu/Python 3.13/Node 22, activada en main, release o workflow manual. Estos workflows están creados; su ejecución remota no se ha efectuado desde esta sesión.
