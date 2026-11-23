# TUPA⚡: Telemetría Unificada de Potencia Argentina

> **Aviso:** Este documento es una traducción. El documento maestro es
> [README.md](README.md); en caso de discrepancia, prevalece ese archivo.

TUPA es un proyecto destinado a agregar y mostrar el estado de la red
eléctrica argentina, pensado para alimentar bots de mensajería y otros sistemas.

Debe su nombre a Tupã, el espíritu guaraní del relámpago, el trueno y las
tormentas. ⚡

> **Trabajo en curso:** Este proyecto se encuentra en una etapa exploratoria.

## Filosofía

En orden de prioridad:

1. Seguridad
2. Corrección
3. Limitar el alcance necesario
4. Pocas dependencias o ninguna

> TUPA es un agregador amplio, no una fuente autoritativa. En sistemas críticos
> debe considerarse solo otra fuente de alertas, no un indicador de salud.

## Diseño

TUPA se ejecuta por sí solo o desde cron y escribe JSON en stdout. Los miembros
se seleccionan mediante argumentos, variables de entorno o configuración JSON, y
por defecto son todos. Cada miembro combina varias fuentes de datos e informa el
peor estado conocido. La salida se puede usar directamente o redirigir a un
archivo para servirla; se espera que cada consumidor sepa seleccionar los
miembros relevantes. Véase [example.json](example.json). En modo demonio, TUPA
sirve un panel HTML en `/` y JSON en `/api`. El diálogo de fuentes del panel
muestra solo las fuentes activas en la instancia.

Configuración: [config.md](config.md) (en inglés). La mayoría de los usuarios no
necesita cambiar nada: los valores por defecto están pensados para funcionar.
Normalmente solo se ajustan la dirección y el puerto de escucha (y a veces la
zona horaria, que por defecto es la de Argentina).

### Ejecutar desde Docker (recomendado)

```sh
docker compose up --build --detach
```

`stack.env` es una plantilla con todas las opciones admitidas y sus valores por
defecto comentados. Opcionalmente, copiala a `.env`, descomentá y modificá los
valores que quieras cambiar antes de iniciar el contenedor.

### Ejecutar desde el código fuente

Desde `src/`:

```sh
cd src
python3 -m tupa
python3 -m unittest discover -s tests
```

Desde la raíz del repositorio, `python3 -m pip install .` también instala el
comando `tupa`.

### Versiones

La versión se declara una sola vez en `pyproject.toml`. Después de actualizarla,
creá una etiqueta de Git con el mismo valor `vX.Y.Z`.

### Fuentes implementadas

Edenor y Edesur se obtienen de los cortes de ENRE y la demanda de CAMMESA. TUPA
es un agregador automatizable, no un reemplazo de los servicios de monitoreo
independientes.

### Topología

El contexto de las fuentes previas está en [topology.md](topology.md) y
[diagram.pu](diagram.pu).

### Estado

Un `member` lleva un `status` (`operational`, `warn`,
`high`, `disaster` o `unknown`), un `comment` y una marca de tiempo `updated`.
Los colores (verde, amarillo, naranja, rojo, gris) son solo indicaciones
visuales. Un `system` tiene su propia marca de tiempo `updated` y un mapa
`members` indexado por nombre de miembro. Las marcas de tiempo son cadenas
ISO 8601 / RFC 3339. Consulte el [esquema CDDL](status.cddl).

ENRE solo publica una hora del día en hora de Argentina (ART): TUPA la combina
con la fecha HTTP `Last-Modified` del archivo y la convierte a la zona horaria
de salida configurada. Sin esa cabecera, `updated` registra cuándo TUPA observó
el dato, sin suponer una fecha. Todas las marcas de tiempo se normalizan a la
zona horaria configurada (por defecto, hora de Argentina).

Los cortes programados son cortes: usan los mismos umbrales de severidad que
cualquier otro corte.

## Contribuciones

- Mandá un pull request debidamente revisado, o al menos un issue bien fundamentado.
- Escribime en Nostr: [fred@hodl.ar](https://njump.me/fred@hodl.ar)
- Revoleame un café por la red Lightning a `fred@lawallet.io`.

## Licencia

Copyright (c) 2026 LU1AJR. Distribuido bajo la
[Licencia BSD 2-Clause](LICENSE).

La licencia cubre el código y la documentación propios de TUPA; los datos
obtenidos de fuentes externas como CAMMESA, ENRE y OCEBA siguen sujetos a sus
respectivos términos.

## FEO

* **ENRE TLS:** solo TLS 1.2 con RSA estático; TUPA usa `@SECLEVEL=1` solo para ENRE, con verificación de certificado activa.
* **ENRE API(n't):** no hay API; los datos están en `data_EDN.js` / `data_EDS.js`.
