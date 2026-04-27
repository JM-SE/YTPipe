# Swagger / OpenAPI Usage Guide

## Objetivo

Este documento explica cómo usar la documentación interactiva de la API de YTPipe:

- Swagger UI: `/docs`
- OpenAPI JSON: `/openapi.json`
- ReDoc: `/redoc`

En `local`, estas rutas son públicas para comodidad.

En `staging` y `production`, estas rutas están protegidas con el mismo bearer token interno usado por los endpoints internos.

---

## 1. URLs principales

Reemplazar `https://ytpipe-staging.onrender.com` por la URL real del servicio Render si es distinta.

```text
Swagger UI:
https://ytpipe-staging.onrender.com/docs

OpenAPI JSON:
https://ytpipe-staging.onrender.com/openapi.json

ReDoc:
https://ytpipe-staging.onrender.com/redoc
```

---

## 2. Header requerido en staging/production

En `staging` y `production`, las rutas anteriores requieren:

```text
Authorization: Bearer <INTERNAL_API_BEARER_TOKEN_REAL>
```

Ejemplo:

```text
Authorization: Bearer abc123token-real-largo
```

Importante:

- El key es `Authorization`.
- El value empieza con `Bearer`, luego un espacio, luego el token real.
- No usar comillas.
- No pegar `Authorization:` dentro del value.

---

## 3. Probar OpenAPI con PowerShell

### 3.1 Definir URL y token

```powershell
$renderUrl = "https://ytpipe-staging.onrender.com"
$token = "<INTERNAL_API_BEARER_TOKEN_REAL>"
$headers = @{ Authorization = "Bearer $token" }
```

### 3.2 Probar `/openapi.json`

```powershell
Invoke-RestMethod `
  -Uri "$renderUrl/openapi.json" `
  -Headers $headers
```

Resultado esperado:

- Devuelve JSON de OpenAPI.
- Debe contener información de endpoints como `/health`, `/status`, `/internal/run-poll`, etc.

### 3.3 Probar que sin token falla

```powershell
Invoke-RestMethod `
  -Uri "$renderUrl/openapi.json"
```

Resultado esperado en staging/production:

```text
401 Invalid internal bearer token.
```

---

## 4. Probar endpoints internos con PowerShell

Aunque Swagger sea más cómodo, estos comandos sirven para validar que el token funciona.

### 4.1 `/status`

```powershell
Invoke-RestMethod `
  -Uri "$renderUrl/status" `
  -Headers $headers
```

### 4.2 Listar canales

```powershell
Invoke-RestMethod `
  -Uri "$renderUrl/internal/channels" `
  -Headers $headers
```

### 4.3 Ejecutar sync de subscriptions

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri "$renderUrl/internal/subscriptions/sync" `
  -Headers $headers
```

### 4.4 Ejecutar poll manual

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri "$renderUrl/internal/run-poll" `
  -Headers $headers
```

---

## 5. Usar Swagger en el navegador con ModHeader

Los navegadores no permiten agregar headers custom manualmente al abrir una URL normal. Por eso, para abrir `/docs` protegido en staging/production, se recomienda usar una extensión como **ModHeader**.

### 5.1 Instalar ModHeader

Chrome Web Store:

```text
https://chromewebstore.google.com/detail/modheader-modify-http-hea/idgpnmonknjnojddfkpgkljpfnnfcklj
```

Sitio del producto:

```text
https://modheader.com/
```

También existen extensiones equivalentes para otros navegadores. La idea es la misma: agregar un header HTTP a las requests del navegador.

### 5.2 Configurar el header

En ModHeader, agregar un request header:

```text
Name: Authorization
Value: Bearer <INTERNAL_API_BEARER_TOKEN_REAL>
```

Ejemplo:

```text
Name: Authorization
Value: Bearer abc123token-real-largo
```

### 5.3 Limitar el header al dominio de Render

Si ModHeader permite filtros por URL, limitarlo a:

```text
https://ytpipe-staging.onrender.com/*
```

o al dominio real del servicio.

Esto evita mandar el token a otros sitios por accidente.

### 5.4 Abrir Swagger

Con ModHeader activado, abrir:

```text
https://ytpipe-staging.onrender.com/docs
```

Resultado esperado:

- Swagger UI carga correctamente.
- Se ven los endpoints de la API.

---

## 6. Usar el botón Authorize dentro de Swagger

Una vez que Swagger carga:

1. Click en **Authorize**.
2. Pegar el bearer token.

Según cómo Swagger muestre el campo, probar primero con el token solo:

```text
<INTERNAL_API_BEARER_TOKEN_REAL>
```

Si algún endpoint responde `401`, probar con el prefijo completo:

```text
Bearer <INTERNAL_API_BEARER_TOKEN_REAL>
```

Después de autorizar, Swagger debería poder ejecutar endpoints protegidos como:

```text
GET /status
POST /internal/subscriptions/sync
GET /internal/channels
POST /internal/run-poll
```

---

## 7. Uso recomendado en staging

Orden recomendado:

1. Verificar `/health` público.
2. Verificar `/openapi.json` con PowerShell y bearer.
3. Configurar ModHeader.
4. Abrir `/docs`.
5. Usar **Authorize** dentro de Swagger.
6. Probar `GET /status`.
7. Probar endpoints internos desde Swagger.

---

## 8. Seguridad y cuidado del token

El `INTERNAL_API_BEARER_TOKEN` permite ejecutar endpoints internos.

Tratarlo como secreto.

Buenas prácticas:

- No pegarlo en chats.
- No subirlo al repo.
- No dejar capturas públicas donde se vea.
- Si se filtra, rotarlo en Render.
- Si se usa ModHeader, limitarlo solo al dominio de YTPipe.
- Desactivar ModHeader cuando no se esté usando.

---

## 9. Troubleshooting

### Swagger devuelve 401

Revisar:

- ModHeader está activado.
- Header name es exactamente `Authorization`.
- Header value es `Bearer <token>`.
- Hay un espacio entre `Bearer` y el token.
- El token coincide con `INTERNAL_API_BEARER_TOKEN` configurado en Render.
- El servicio fue redeployado después de cambiar variables.

### `/docs` carga pero los endpoints internos devuelven 401

Esto puede pasar si:

- ModHeader permitió cargar `/docs`, pero no se usó **Authorize** dentro de Swagger.
- El valor pegado en **Authorize** no es correcto.

Solución:

1. Click en **Authorize**.
2. Pegar el token.
3. Reintentar el endpoint.

### PowerShell funciona pero navegador no

Probablemente el navegador no está enviando el header.

Revisar ModHeader.

### Navegador funciona pero PowerShell no

Revisar que `$headers` esté armado así:

```powershell
$headers = @{ Authorization = "Bearer $token" }
```

---

## 10. Comportamiento por entorno

```text
APP_ENV=local:
  /docs público
  /openapi.json público
  /redoc público

APP_ENV=staging:
  /docs requiere bearer
  /openapi.json requiere bearer
  /redoc requiere bearer

APP_ENV=production:
  /docs requiere bearer
  /openapi.json requiere bearer
  /redoc requiere bearer
```

`/health` permanece público en todos los entornos.
