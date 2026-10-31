---
titulo: Bases de datos y respaldos
categoria: arquitectura
etiquetas: postgresql, respaldos, recuperacion, retencion
---

# Plataforma Atlas: bases de datos y respaldos

## Respaldos

La base PostgreSQL de `pagos` tiene un respaldo completo por día, a las 03:00, y un archivo continuo del registro de transacciones. Con ese esquema, la base se puede restaurar a cualquier minuto de los últimos 35 días. Los respaldos se guardan cifrados en otra región.

## Objetivos de recuperación

El objetivo de punto de recuperación (RPO) es de 5 minutos: ante un desastre, no deberían perderse más que los últimos 5 minutos de datos. El objetivo de tiempo de recuperación (RTO) es de 1 hora. Una vez por trimestre se restaura un respaldo real en un entorno aislado para comprobar que ambos objetivos se cumplen.

## Conmutación por error

Si la instancia principal de PostgreSQL deja de responder durante 30 segundos, Patroni promueve la réplica sincrónica a principal. La operación tarda menos de un minuto y las conexiones se redirigen a través de PgBouncer, sin cambios en los servicios.

## Retención de datos

Los cobros se conservan 10 años en la base principal por obligación regulatoria. Los eventos de RabbitMQ no se conservan: una vez procesados, se eliminan. Los registros de aplicación duran 30 días, como indica la documentación de observabilidad.

## Datos personales

Los correos y nombres de los compradores se guardan cifrados a nivel de columna. Cuando un comprador pide la eliminación de sus datos, se reemplazan por valores anónimos dentro de los 30 días, salvo lo que la regulación obligue a conservar. Las consultas analíticas se hacen sobre una copia sin datos personales que se actualiza cada noche.
