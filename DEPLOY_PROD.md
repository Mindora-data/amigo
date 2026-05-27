# Deploy production privado

Objetivo: exponer `amigo` solo para uso propio. No hay registro abierto.

## Variables obligatorias

Configura estos valores en el gestor de secretos del proveedor o en `.env.local` local con permisos `600`:

```sh
NINO_ENV=prod
NINO_REQUIRE_SESSION=true
NINO_PASSWORD_HASH=<hash generado por scripts/ninoctl configure-password --password-stdin>
NINO_SESSION_PEPPER=<secreto largo aleatorio para hash de tokens>
NINO_LLM_PROVIDER=claude
NINO_KEYCHAIN_SERVICE=nino-anthropic
# o, para DeepSeek:
# NINO_LLM_PROVIDER=deepseek
# DEEPSEEK_API_KEY=<secreto del proveedor>
```

No guardes `ANTHROPIC_API_KEY`, `DEEPSEEK_API_KEY`, `NINO_PASSWORD_HASH`, `NINO_SESSION_PEPPER` ni contraseñas en Git.

## Configurar contraseña

La contraseña se configura por stdin para no dejarla en el historial:

```sh
cd ~/Developer/bebe
read -rs "NINO_PASSWORD?Password: "
printf '%s' "$NINO_PASSWORD" | scripts/ninoctl configure-password --password-stdin
unset NINO_PASSWORD
```

El comando escribe solo `NINO_PASSWORD_HASH`, no la contraseña.

## Sesiones

La UI final usa cookie `HttpOnly`, `SameSite=Strict` y, en producción, `Secure`.

También se acepta `X-Nino-Session` para clientes internos/tests, pero el navegador de `/user` no guarda el token en `localStorage`. Las sesiones duran 7 días y se refrescan con el uso. `POST /session/logout` invalida la sesión y limpia la cookie.

## HTTPS

En `NINO_ENV=prod`, el servidor rechaza tráfico que no llegue con:

```http
X-Forwarded-Proto: https
```

Ponlo detrás de un proxy/proveedor que termine TLS y reenvíe esa cabecera.

## Consola interna

`/app` queda deshabilitada en producción. Usa `/user` o `/chat` para la interfaz final. Para operaciones internas, entra por SSH/local y usa `scripts/ninoctl`.

## Cabeceras de seguridad

En producción se envían:

- `Strict-Transport-Security`
- `X-Content-Type-Options: nosniff`
- `X-Frame-Options: DENY`
- `Content-Security-Policy` mínima para la UI local

## Intentos sospechosos

Los eventos de acceso se guardan en memoria del proceso como `security_audit`:

- `login_ok`
- `login_failed`
- `login_blocked`
- `session_expired`

Si ves varios `login_failed` o `login_blocked` desde una IP:

1. Cambia la contraseña con `scripts/ninoctl configure-password --password-stdin`.
2. Cambia `NINO_SESSION_PEPPER` en el proveedor.
3. Reinicia el servicio para invalidar sesiones en memoria.
4. Revisa que el proxy solo expone HTTPS.

## Validación antes de exponer

```sh
scripts/ninoctl final-audit
scripts/ninoctl product-status
```

No expongas si `product-status` no está en `ready`.
