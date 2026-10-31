---
titulo: Conciliación y liquidaciones
categoria: finanzas
etiquetas: conciliacion, liquidaciones, bancos, diferencias
---

# Plataforma Atlas: conciliación y liquidaciones

## Conciliación diaria

El proceso de conciliación compara los cobros registrados en Atlas con los archivos que envían los bancos. Los archivos llegan por SFTP antes de la 01:00 y el proceso arranca a las 02:00. Si a esa hora falta el archivo de algún banco, el proceso espera hasta las 06:00 reintentando cada 30 minutos, y después avisa al equipo de finanzas.

## Tipos de diferencia

Una diferencia es cualquier cobro que no coincide entre Atlas y el banco. Las diferencias de hasta 10 pesos se atribuyen a redondeo y se cierran solas. Un cobro que figura en el banco y no en Atlas se llama cobro huérfano y es la diferencia más grave, porque indica dinero recibido sin un registro que lo respalde: debe resolverse dentro de las 24 horas.

## Liquidaciones a tiendas

Una liquidación transfiere a la tienda lo cobrado, menos comisiones y reembolsos. Las tiendas estándar cobran a las 48 horas hábiles y las tiendas con más de un año de antigüedad y sin contracargos altos pueden pasar al plazo de 24 horas. Las liquidaciones se generan a las 09:00 y no se procesan los feriados bancarios.

## Reserva de garantía

A las tiendas de alto riesgo se les retiene un 10 % de cada liquidación durante 90 días, como garantía frente a contracargos. La reserva se libera sola al vencer el plazo.

## Cierre contable

El cierre contable del mes se hace el tercer día hábil del mes siguiente. Una vez cerrado, ningún movimiento de ese mes se puede modificar: las correcciones se registran como ajustes en el mes en curso.
