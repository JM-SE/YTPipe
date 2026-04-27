# Deploy staging desde cero: Neon + Render + Google OAuth

## Objetivo

Levantar una versión **staging** de YTPipe en Render.

- Base de datos: **Neon Postgres**
- App: **Render Web Service**
- Email: **fake mode** (`EMAIL_DELIVERY_MODE=fake`), sin emails reales todavía
- Migraciones: **manuales**
- Cron: configurar después del smoke test manual
- Resend real: pendiente para producción final

---

## 1. Crear base de datos en Neon

### 1.1 Entrar a Neon

Abrir:

```text
https://console.neon.tech/
```

Loguearse o crear cuenta.

### 1.2 Crear proyecto

1. Click en **New Project**.
2. Configurar:
   - Name: `ytpipe-staging`
   - Region: la más cercana a vos o a Render
   - Postgres version: default
3. Click en **Create Project**.

### 1.3 Obtener connection string

Dentro del proyecto:

1. Ir a **Dashboard**.
2. Buscar **Connection Details**.
3. Elegir:
   - Branch: `main`
   - Database: default, probablemente `neondb`
   - Role: default
4. Copiar la connection string.

Neon suele dar algo así:

```text
postgresql://USER:PASSWORD@HOST/neondb?sslmode=require
```

La app necesita SQLAlchemy con `psycopg`, entonces cambiar:

```text
postgresql://
```

por:

```text
postgresql+psycopg://
```

Ejemplo final:

```text
postgresql+psycopg://USER:PASSWORD@ep-xxxx.us-east-2.aws.neon.tech/neondb?sslmode=require
```

Guardar este valor como `DATABASE_URL`.

---

## 2. Crear app en Render desde Blueprint

### 2.1 Entrar a Render

Abrir:

```text
https://dashboard.render.com/
```

Loguearse o crear cuenta.

### 2.2 Conectar el repo

Si GitHub todavía no está conectado:

1. Ir a:

```text
https://dashboard.render.com/select-repo
```

2. Conectar GitHub.
3. Dar acceso al repo `YTPipe`.

### 2.3 Crear Blueprint

Como el repo tiene `render.yaml`, usar Blueprint:

1. Abrir:

```text
https://dashboard.render.com/blueprints
```

2. Click en **New Blueprint Instance**.
3. Elegir el repo `YTPipe`.
4. Render debería detectar `render.yaml`.
5. Confirmar creación.

El servicio debería llamarse:

```text
ytpipe-staging
```

---

## 3. Configurar variables de entorno en Render

En Render:

1. Entrar al servicio `ytpipe-staging`.
2. Ir a **Environment**.
3. Cargar o verificar estas variables.

### 3.1 Variables no secretas

Estas deberían venir desde `render.yaml`, pero conviene verificarlas:

```text
APP_NAME=ytpipe
APP_ENV=staging
EMAIL_DELIVERY_MODE=fake
POLL_QUOTA_DAILY_BUDGET=500
POLL_QUOTA_SAFETY_STOP_ENABLED=true
```

### 3.2 Variables secretas

Cargar manualmente:

```text
APP_SECRET_KEY=<generar-un-secreto-largo>
INTERNAL_API_BEARER_TOKEN=<generar-otro-secreto-largo>
DATABASE_URL=<connection-string-de-neon>
GOOGLE_CLIENT_ID=<pendiente-del-paso-google>
GOOGLE_CLIENT_SECRET=<pendiente-del-paso-google>
GOOGLE_REDIRECT_URI=<pendiente-hasta-tener-url-render>
RESEND_API_KEY=dummy-for-staging
RESEND_FROM_EMAIL=dummy@example.com
```

Para staging, como se usa:

```text
EMAIL_DELIVERY_MODE=fake
```

Resend no se usa realmente. Se pueden poner valores dummy no vacíos:

```text
RESEND_API_KEY=dummy-for-staging
RESEND_FROM_EMAIL=dummy@example.com
```

### 3.3 Generar secretos

En PowerShell local:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Usar un valor para:

```text
APP_SECRET_KEY
```

Y otro distinto para:

```text
INTERNAL_API_BEARER_TOKEN
```

Guardar el `INTERNAL_API_BEARER_TOKEN`; se usa para `/status`, `/internal/subscriptions/sync`, `/internal/channels` y `/internal/run-poll`.

---

## 4. Obtener URL pública de Render

Cuando el servicio esté creado, Render dará una URL similar a:

```text
https://ytpipe-staging.onrender.com
```

Guardar ese valor como `RENDER_URL`.

Ejemplo:

```text
RENDER_URL=https://ytpipe-staging.onrender.com
```

---

## 5. Configurar Google OAuth

### 5.1 Entrar a Google Cloud Console

Abrir:

```text
https://console.cloud.google.com/
```

### 5.2 Crear o elegir proyecto

1. Usar el selector de proyecto arriba a la izquierda.
2. Crear un proyecto nuevo o elegir uno existente.
3. Nombre recomendado:

```text
YTPipe Staging
```

### 5.3 Habilitar YouTube Data API v3

Abrir:

```text
https://console.cloud.google.com/apis/library/youtube.googleapis.com
```

Con el proyecto seleccionado:

1. Click en **Enable**.

### 5.4 Configurar OAuth consent screen

Abrir:

```text
https://console.cloud.google.com/apis/credentials/consent
```

Configuración recomendada para staging:

- User type: **External**
- App name: `YTPipe Staging`
- User support email: tu email
- Developer contact: tu email

Scopes mínimos:

- `openid`
- `https://www.googleapis.com/auth/userinfo.email`
- `https://www.googleapis.com/auth/youtube.readonly`

Si Google pide test users:

1. Agregar tu email como test user.
2. Guardar.

### 5.5 Crear OAuth Client ID

Abrir:

```text
https://console.cloud.google.com/apis/credentials
```

1. Click en **Create Credentials**.
2. Elegir **OAuth client ID**.
3. Application type:

```text
Web application
```

4. Name:

```text
YTPipe Staging Web Client
```

5. Authorized redirect URIs:

Usar la URL de Render:

```text
https://ytpipe-staging.onrender.com/auth/callback
```

Si la URL de Render es otra, usar exactamente esa:

```text
https://<tu-render-host>/auth/callback
```

6. Guardar.

Google entregará:

```text
GOOGLE_CLIENT_ID
GOOGLE_CLIENT_SECRET
```

---

## 6. Completar Google env vars en Render

Volver a Render:

```text
https://dashboard.render.com/
```

En el servicio `ytpipe-staging` -> **Environment**, cargar:

```text
GOOGLE_CLIENT_ID=<client-id-de-google>
GOOGLE_CLIENT_SECRET=<client-secret-de-google>
GOOGLE_REDIRECT_URI=https://<tu-render-host>/auth/callback
```

Ejemplo:

```text
GOOGLE_REDIRECT_URI=https://ytpipe-staging.onrender.com/auth/callback
```

Importante: tiene que coincidir exactamente con el redirect URI configurado en Google.

Después de guardar env vars, hacer **Manual Deploy** o **Redeploy**.

---

## 7. Ejecutar migraciones manuales contra Neon

Desde la máquina local, en el repo:

```powershell
cd C:\Users\User\Desktop\Projects\YTPipe
```

Activar venv:

```powershell
.venv\Scripts\activate
```

Setear temporalmente el `DATABASE_URL` de Neon:

```powershell
$env:DATABASE_URL = "postgresql+psycopg://USER:PASSWORD@HOST/neondb?sslmode=require"
```

Ejecutar migraciones:

```powershell
python -m alembic upgrade head
```

Si termina sin error, Neon ya tiene las tablas.

---

## 8. Smoke test público: `/health`

En PowerShell:

```powershell
$renderUrl = "https://ytpipe-staging.onrender.com"
Invoke-RestMethod "$renderUrl/health"
```

Esperado: respuesta OK, por ejemplo:

```json
{
  "status": "ok"
}
```

---

## 9. Smoke test protegido: `/status`

Usar el token real configurado en Render:

```powershell
$renderUrl = "https://ytpipe-staging.onrender.com"
$token = "<INTERNAL_API_BEARER_TOKEN_REAL>"
$headers = @{ Authorization = "Bearer $token" }

Invoke-RestMethod `
  -Uri "$renderUrl/status" `
  -Headers $headers
```

Esperado:

- Responde JSON.
- No devuelve `Invalid internal bearer token`.
- `ready` puede estar `false` si todavía no se hizo OAuth/import.

---

## 9a. Usar Swagger/OpenAPI protegido en staging

En staging, estas rutas están disponibles pero protegidas con bearer:

```text
https://ytpipe-staging.onrender.com/docs
https://ytpipe-staging.onrender.com/openapi.json
https://ytpipe-staging.onrender.com/redoc
```

Requieren el mismo header:

```text
Authorization: Bearer <INTERNAL_API_BEARER_TOKEN_REAL>
```

Para probar desde PowerShell:

```powershell
$renderUrl = "https://ytpipe-staging.onrender.com"
$token = "<INTERNAL_API_BEARER_TOKEN_REAL>"
$headers = @{ Authorization = "Bearer $token" }

Invoke-RestMethod `
  -Uri "$renderUrl/openapi.json" `
  -Headers $headers
```

Para abrir `/docs` en el navegador en staging/production, el navegador tiene que enviar el header `Authorization`. Una forma práctica es usar una extensión tipo **ModHeader** y configurar:

```text
Header name: Authorization
Header value: Bearer <INTERNAL_API_BEARER_TOKEN_REAL>
```

Luego abrir:

```text
https://ytpipe-staging.onrender.com/docs
```

Una vez cargado Swagger, usar el botón **Authorize** para pegar el bearer token y ejecutar endpoints protegidos desde la UI.

En local, `/docs`, `/openapi.json` y `/redoc` quedan públicos para comodidad.

---

## 10. Probar OAuth en staging

Abrir en navegador:

```text
https://ytpipe-staging.onrender.com/auth/google
```

Flujo esperado:

1. Redirige a Google.
2. Aceptar permisos.
3. Vuelve a:

```text
https://ytpipe-staging.onrender.com/auth/callback
```

4. La app responde indicando que OAuth fue guardado y que falta sync de subscriptions.

---

## 11. Ejecutar sync de subscriptions en staging

En PowerShell:

```powershell
$renderUrl = "https://ytpipe-staging.onrender.com"
$token = "<INTERNAL_API_BEARER_TOKEN_REAL>"
$headers = @{ Authorization = "Bearer $token" }

Invoke-RestMethod `
  -Method Post `
  -Uri "$renderUrl/internal/subscriptions/sync" `
  -Headers $headers
```

Esperado:

- Importa catálogo de canales.
- No monitorea ninguno automáticamente.
- No crea videos.
- No crea deliveries.

---

## 12. Listar canales importados

```powershell
Invoke-RestMethod `
  -Uri "$renderUrl/internal/channels" `
  -Headers $headers
```

Buscar un canal y anotar su:

```text
channel_id
```

Importante: este es el ID local numérico, no el YouTube channel ID.

---

## 13. Activar monitoreo para un canal

Reemplazar `123` por un `channel_id` real:

```powershell
$body = @{ is_monitored = $true } | ConvertTo-Json

Invoke-RestMethod `
  -Method Patch `
  -Uri "$renderUrl/internal/channels/123/monitoring" `
  -Headers $headers `
  -ContentType "application/json" `
  -Body $body
```

Esperado:

```json
{
  "channel_id": 123,
  "is_monitored": true
}
```

---

## 14. Ejecutar poll manual

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri "$renderUrl/internal/run-poll" `
  -Headers $headers
```

Primer poll esperado:

```json
{
  "run_outcome": "success",
  "channels_processed": 1,
  "channels_failed": 0,
  "baselines_established": 1,
  "new_videos_detected": 0,
  "quota_blocked": false
}
```

En el primer poll se establece baseline. No se envía email.

---

## 15. Revisar `/status` otra vez

```powershell
Invoke-RestMethod `
  -Uri "$renderUrl/status" `
  -Headers $headers
```

Ahora deberían verse datos en:

```text
subscription_sync
polling
quota
channels
```

---

## 16. Configurar cron-job.org después del smoke test

Entrar a:

```text
https://cron-job.org/
```

1. Crear cuenta o login.
2. Crear nuevo cronjob.
3. URL:

```text
https://ytpipe-staging.onrender.com/internal/run-poll
```

4. Method:

```text
POST
```

5. Header:

```text
Authorization: Bearer <INTERNAL_API_BEARER_TOKEN_REAL>
```

6. Schedule recomendado inicial:

```text
Every 1 hour
```

No usar cada 10 minutos todavía.

---

## 17. Qué NO hacemos todavía

Todavía no hacemos production final porque falta:

- Configurar Resend real.
- Verificar dominio/sender en Resend.
- Cambiar:

```text
APP_ENV=production
EMAIL_DELIVERY_MODE=resend
```

- Usar:

```text
RESEND_API_KEY=<real>
RESEND_FROM_EMAIL=<sender-verificado>
```

---

## Checklist final staging

- [x] Neon project creado.
- [x] `DATABASE_URL` tiene `postgresql+psycopg://`.
- [x] `DATABASE_URL` tiene `sslmode=require`.
- [x] Render Blueprint creado desde `render.yaml`.
- [x] Secrets cargados en Render.
- [x] Google OAuth redirect coincide exactamente con Render.
- [x] Migraciones ejecutadas manualmente.
- [x] `/health` responde.
- [x] `/status` responde con bearer.
- [x] OAuth funciona.
- [x] Subscription sync funciona.
- [x] Canales se listan.
- [x] Se puede activar monitoreo.
- [x] Primer poll establece baseline.
- [x] cron-job.org configurado solo después de validar manualmente.

Nota: la primera ejecución de cron-job.org llegó a la API, pero devolvió 500 por un problema de conexión a base de datos `AdminShutdown`/conexión stale. Esto se trackea por separado y no significa que la configuración de cron haya fallado.
