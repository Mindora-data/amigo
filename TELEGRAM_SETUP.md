# Telegram privado

Telegram es solo otro cliente de `amigo`. La memoria, perfil, recordatorios,
proactividad y privacidad siguen en el backend local.

## Crear el bot

1. Abre Telegram y habla con `@BotFather`.
2. Ejecuta `/newbot`.
3. Guarda el token solo fuera de Git.

## Guardar el token

Opción `.env.local` local con permisos `600`:

```sh
cd ~/Developer/bebe
read -rs "NINO_TELEGRAM_BOT_TOKEN?Telegram token: "
printf '\nNINO_TELEGRAM_BOT_TOKEN=%s\n' "$NINO_TELEGRAM_BOT_TOKEN" >> .env.local
chmod 600 .env.local
unset NINO_TELEGRAM_BOT_TOKEN
```

Opción Keychain:

```sh
security add-generic-password -U -a "$USER" -s nino-telegram -w "<token>"
printf '\nNINO_TELEGRAM_KEYCHAIN_SERVICE=nino-telegram\n' >> .env.local
chmod 600 .env.local
```

No guardes el token en el repo.

Si el backend está en modo producción con contraseña, añade también una contraseña
de backend para que el bot pueda abrir su sesión privada:

```sh
read -rs "NINO_TELEGRAM_BACKEND_PASSWORD?Backend password: "
printf '\nNINO_TELEGRAM_BACKEND_PASSWORD=%s\n' "$NINO_TELEGRAM_BACKEND_PASSWORD" >> .env.local
chmod 600 .env.local
unset NINO_TELEGRAM_BACKEND_PASSWORD
```

## Vincular un chat

Genera un código de un solo uso para tu usuario:

```sh
cd ~/Developer/bebe
scripts/ninoctl telegram --create-link-code mindora
```

En el chat de Telegram con el bot, envía:

```text
/link CODIGO
```

Un chat no vinculado no accede a ninguna memoria.

## Lanzar/parar con launchd

El backend `nino` debe estar arrancado primero.

```sh
cd ~/Developer/bebe
scripts/ninoctl telegram-launchd install
scripts/ninoctl telegram-launchd status
scripts/ninoctl telegram-launchd stop
scripts/ninoctl telegram-launchd start
```

Logs:

```sh
tail -f data/nino-telegram.log
tail -f data/nino-telegram.err.log
```

## Seguridad

- Long polling solo: no abre puertos ni usa webhooks.
- `chat_id` se mapea a un único `user_id` en `telegram_link`.
- El bot llama a las rutas privadas `/users/{user_id}/agents/nino/...`.
- Si `NINO_REQUIRE_SESSION=true`, el bot inicia sesión contra el backend y usa `X-Nino-Session`.
- La proactividad sale por Telegram solo después de pasar por las reglas del backend.

## Grupos

Puedes añadir el bot a un grupo, pero usa reglas más estrictas:

- Si el grupo habla entre sí, amigo no responde.
- Responde solo si lo mencionan (`@nombre_del_bot`), usan comando o responden a un mensaje suyo.
- Un grupo usa memoria separada `telegram-group-<chat_id>`, no memoria privada.
- Si una persona vinculó su cuenta individual, sus mensajes dirigidos al bot pueden usar su memoria privada.
- Una persona no vinculada en grupo nunca accede a memoria privada.
