---
titulo: Reembolsos y contracargos
categoria: operaciones
etiquetas: reembolsos, contracargos, devoluciones, plazos
---

# Plataforma Atlas: reembolsos y contracargos

## Tipos de reembolso

Un reembolso devuelve al comprador todo o parte de un cobro aprobado. Atlas distingue dos tipos. El reembolso total devuelve el importe completo y cierra el cobro. El reembolso parcial devuelve una parte, y un mismo cobro admite hasta 5 reembolsos parciales mientras la suma no supere el importe original.

Los reembolsos se piden con `POST /v2/cobros/{id}/reembolsos` y los procesa el servicio `reembolsos`, que los ejecuta en lotes cada 15 minutos.

## Plazos

Una tienda puede pedir un reembolso hasta 180 días después de aprobado el cobro. Pasado ese plazo, el proveedor de cobros lo rechaza y la devolución debe resolverse por fuera de Atlas. El dinero llega a la tarjeta del comprador entre 5 y 10 días hábiles después de procesado, según el banco emisor.

## Comisiones

Atlas no cobra comisión por reembolsar, pero tampoco devuelve a la tienda la comisión de procesamiento del cobro original. Esa comisión es del 2,9 % más 30 centavos por operación.

## Contracargos

Un contracargo ocurre cuando el comprador desconoce un cobro ante su banco. El importe se descuenta de inmediato del saldo de la tienda, que tiene 7 días corridos para presentar pruebas de la entrega. Cada contracargo tiene un costo administrativo de 15 dólares, aunque la tienda gane la disputa.

Si la tasa de contracargos de una tienda supera el 1 % de sus operaciones durante dos meses seguidos, la tienda entra en un programa de monitoreo y debe presentar un plan de reducción. Si llega al 2 %, se suspende su capacidad de cobrar hasta que el equipo de riesgo la revise.

## Reembolsos fallidos

Un reembolso puede fallar si la tarjeta original fue dada de baja. En ese caso, el estado pasa a `reembolso_fallido` y la tienda recibe un webhook para gestionar la devolución por transferencia bancaria. Ningún reembolso fallido se reintenta en forma automática.
