# TUPA: Telemetría Unificada de Potencia Argentina

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

TUPA se ejecuta por sí solo o desde cron y escribe JSON en stdout. Las fuentes
se seleccionan mediante argumentos, variables de entorno o configuración, y por
defecto son todas. La salida se puede usar directamente o redirigir a un archivo
para servirla; se espera que cada consumidor sepa seleccionar de las fuentes
disponibles aquellas que le resultan relevantes. Véase
[example.json](example.json).

Configuración: [config.md](config.md) (en inglés).

### Fuentes iniciales

El trabajo comienza con las siguientes fuentes:

- Edenor
- Edesur
- Transener
- SADI
- CAMMESA
- SACME
- Edelap
- CortesDeLuz.com.ar (CDL, verificación cruzada)

> **Relación con CDL:** CDL es útil, pero puede no estar disponible, su código
> es cerrado y no puede ejecutarse de forma independiente. TUPA es su
> complemento automatizable, no un reemplazo: aporta una salida de estado
> automatizable y usa CDL para control cruzado cuando otras fuentes no están
> disponibles. Las propias fuentes de CDL se muestran en el
> [diagrama](diagram.pu).

### Topología

- [Topología del sistema y dependencias previas](topology.md)
- [diagrama](diagram.pu)

### Estado

Un `member` lleva un `status` (`operational`, `warn`,
`high`, `disaster` o `unknown`), un `comment` y una marca de tiempo `updated`.
Los colores (verde, amarillo, naranja, rojo, gris) son solo indicaciones
visuales. Un `system` tiene su propia marca de tiempo `updated` y un mapa
`members` indexado por nombre de miembro. Las marcas de tiempo son cadenas
ISO 8601 / RFC 3339. Consulte el [esquema CDDL](status.cddl).

Los eventos de mantenimiento se tratan un nivel por debajo de su alcance físico.

## Tareas

- [X] Documentación en español
- [ ] POC
  - stdlib de Python 3, invocable una vez para cron o como demonio HTTP
  - Docker con python:3.12-slim
  - Implementar salida JSON
  - Implementar las fuentes Edenor y Edesur
- [ ] MVP
  - Agregar salida HTML básica
  - Implementar webhooks salientes o disparadores
- [ ] Piloto
  - Agregar salida SVG con clases
  - Agregar las fuentes mapeadas en la topología: Transener, SADI, CAMMESA, SACME
  - Agregar la fuente Edelap
- [ ] Otras jurisdicciones argentinas

Las fuentes iniciales están planificadas en este orden; CDL es solo de
validación y no tiene tarea de implementación.

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
