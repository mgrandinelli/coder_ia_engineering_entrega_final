---
titulo: Alta y gestión de tiendas
categoria: negocio
etiquetas: tiendas, alta, kyc, limites, planes
---

# Plataforma Atlas: alta y gestión de tiendas

## Alta de una tienda

Para operar, una tienda debe completar la verificación de identidad del comercio. Presenta su constancia fiscal, el estatuto de la sociedad y una cuenta bancaria a su nombre. La verificación tarda hasta 3 días hábiles. Mientras tanto, la tienda puede integrarse con el entorno de pruebas, pero no puede cobrar en producción.

## Planes y límites

Hay tres planes. El plan Inicial permite hasta 5 millones de pesos por mes, el plan Crecimiento hasta 50 millones y el plan Corporativo no tiene tope. Al llegar al 90 % del límite mensual, la tienda recibe un aviso; al alcanzarlo, los cobros nuevos se rechazan hasta el mes siguiente o hasta que cambie de plan.

El límite de 600 solicitudes por minuto rige para todos los planes, salvo el Corporativo, que puede negociar uno mayor.

## Usuarios y permisos

Cada tienda administra sus usuarios con tres roles. El rol Administrador gestiona todo, incluidas las credenciales de la API. El rol Finanzas ve liquidaciones y puede pedir reembolsos. El rol Lectura solo consulta. Toda tienda debe tener al menos dos administradores, para no perder el acceso si uno se va.

## Rotación de credenciales

Las credenciales de la API de una tienda no vencen, pero se recomienda rotarlas una vez por año. Al generar credenciales nuevas, las anteriores siguen activas durante 72 horas, para poder migrar sin cortar el servicio.

## Baja de una tienda

Cuando una tienda se da de baja, deja de cobrar de inmediato, pero sus datos se conservan 5 años por obligación fiscal. Los reembolsos y contracargos de cobros anteriores se siguen procesando durante 180 días, y el saldo restante se liquida después de ese plazo.
