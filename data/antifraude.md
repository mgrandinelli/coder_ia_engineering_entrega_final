---
titulo: Prevención de fraude
categoria: riesgo
etiquetas: antifraude, riesgo, reglas, 3ds
---

# Plataforma Atlas: prevención de fraude

## Puntaje de riesgo

Antes de autorizar un cobro, el servicio `antifraude` le asigna un puntaje de 0 a 100. El puntaje combina un modelo de aprendizaje automático con reglas fijas, y debe calcularse en menos de 150 milisegundos para no demorar el cobro.

Hasta 40 puntos, el cobro se autoriza directamente. Entre 41 y 70, se exige una verificación adicional con 3-D Secure. Desde 71, el cobro se rechaza con el código `ATL-4013`.

## Reglas fijas

Las reglas fijas se aplican aunque el modelo dé un puntaje bajo. Se rechaza cualquier tarjeta usada en más de 5 cobros en 10 minutos, y cualquier cobro cuyo país de emisión de la tarjeta figure en la lista de países bloqueados. Un cobro de más de 500.000 pesos desde un dispositivo nuevo pasa siempre por verificación adicional.

## Reentrenamiento del modelo

El modelo se reentrena una vez por mes con los contracargos confirmados de los últimos 12 meses. Antes de reemplazar al modelo vigente, la versión nueva corre dos semanas en modo sombra: califica las operaciones reales sin afectar el resultado, y solo se promueve si detecta al menos la misma cantidad de fraude con no más rechazos de operaciones legítimas.

## Listas de bloqueo

Las listas de bloqueo guardan identificadores de tarjetas, dispositivos y correos asociados a fraude confirmado. Las administra el equipo de riesgo, y cada entrada vence a los 24 meses si no vuelve a registrar actividad sospechosa.

## Revisión manual

Los cobros de tiendas nuevas que superen los 300.000 pesos durante sus primeros 30 días van a una cola de revisión manual. El equipo de riesgo los resuelve en un máximo de 2 horas en horario laboral. Si nadie los resuelve en ese plazo, el cobro se aprueba para no perjudicar a la tienda.
