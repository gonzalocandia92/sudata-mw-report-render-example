# Sudata PBI — Integración de reportes Power BI

Guía para autenticar con la API de Sudata y renderizar reportes Power BI embebidos.

---

## Dos tokens

La integración involucra dos tokens con ciclos de vida muy distintos.

El primero es el **JWT de Sudata**, que se obtiene una sola vez con las credenciales de la empresa (`POST /private/login`) y es de larga duración. Se usa exclusivamente para autenticar las llamadas a la API de Sudata — listado de reportes, configuración de embed, etc.

El segundo es el **Access Token de Power BI**, que devuelve `/private/report-config` junto con la URL de embed. Este token lo emite Azure AD directamente y vence en minutos.

---

## Endpoints

### `POST /private/login`

Autentica la empresa y devuelve el JWT de Sudata.

**Request:**
```json
{
  "client_id": "tu-client-id",
  "client_secret": "tu-client-secret"
}
```

**Response:**
```json
{
  "access_token": "eyJhbGci...",
  "token_type": "Bearer",
  "expires_in": 3600
}
```

El `access_token` resultante se usa como `Bearer` header en todos los endpoints siguientes.

---

### `GET /private/reports`

Devuelve la lista de reportes disponibles para la empresa autenticada.

**Headers:**
```
Authorization: Bearer {access_token_de_sudata}
```

**Response:**
```json
{
  "empresa_id": 3,
  "empresa_nombre": "Mi Empresa SA",
  "reports": [
    { "id": 1, "name": "Reporte Ventas" },
    { "id": 2, "name": "Reporte Inventarios" }
  ]
}
```

---

### `GET /private/report-config?report_id={id}`

Devuelve la configuración necesaria para embeber un reporte específico. Incluye el token de Power BI de corta duración.

**Headers:**
```
Authorization: Bearer {access_token_de_sudata}
```

**Response:**
```json
{
  "accessToken": "eyJ0eXAiOiJKV1Qi...",
  "embedUrl": "https://app.powerbi.com/reportEmbed?reportId=...&groupId=...",
  "reportId": "8ac22f5b-...",
  "workspaceId": "adf27877-..."
}
```

> ⚠️ Este `accessToken` es el token de Azure AD de Power BI. Vence en minutos. Llamar este endpoint justo antes de cada render, nunca almacenarlo.

---

## Flujo completo

```
1. POST /private/login
        ↓
   JWT de Sudata (cachear ~1h)
        ↓
2. GET /private/reports
        ↓
   Lista de reportes disponibles
        ↓
3. Usuario elige un reporte
        ↓
4. GET /private/report-config?report_id=X   ← siempre fresh
        ↓
   { accessToken, embedUrl, reportId, workspaceId }
        ↓
5. powerbi.embed(container, config)
```

---

## Renderizar con el SDK de Power BI

El token que devuelve `/report-config` es de tipo **AAD** (Azure Active Directory), no un Embed token. Esto es importante al configurar el SDK:

```html
<script src="https://cdn.jsdelivr.net/npm/powerbi-client@2.23.1/dist/powerbi.min.js"></script>
```

```javascript
const models = window['powerbi-client'].models;

const config = {
  type: 'report',
  tokenType: models.TokenType.Aad,   // ← AAD, no Embed
  accessToken: cfg.accessToken,       // ← viene de /report-config
  embedUrl: cfg.embedUrl,
  id: cfg.reportId,
  settings: {
    panes: {
      filters: { visible: false },
      pageNavigation: { visible: true }
    }
  }
};

const report = powerbi.embed(document.getElementById('contenedor'), config);

report.on('loaded', () => console.log('Reporte cargado'));
report.on('error', (e) => console.error(e.detail));
```

El `contenedor` debe tener dimensiones explícitas (`width` y `height` o `100%` con altura definida por el padre).

---

## Implementación Flask (este proyecto)

```
pbi_app/
├── app.py               # Backend: 3 rutas proxy hacia la API de Sudata
└── templates/
    └── index.html       # Frontend: sidebar con lista + área de embed
```

### Variables de entorno

| Variable | Descripción | Default |
|----------|-------------|---------|
| `CLIENT_ID` | Client ID de la empresa | — |
| `CLIENT_SECRET` | Client Secret de la empresa | — |
| `SECRET_KEY` | Clave para firmar la session Flask | `dev-secret` |

### Correr en desarrollo

```bash
pip install flask requests
CLIENT_ID=xxx CLIENT_SECRET=yyy python app.py
```

### Rutas del backend

| Ruta | Descripción |
|------|-------------|
| `GET /` | Sirve el dashboard |
| `GET /api/reports` | Proxy a `/private/reports` |
| `GET /api/report-config?report_id=X` | Proxy a `/private/report-config` (siempre fresh) |

El JWT de Sudata se cachea en la session de Flask y se renueva automáticamente si recibe un 401.

---

## Errores comunes

**`LoadReportFailed`** — El `accessToken` de Power BI venció o es inválido. Llamar `/report-config` de nuevo justo antes de embeber.

**`TokenType incorrecto`** — El token de `/report-config` es AAD. Usar `models.TokenType.Aad`, no `TokenType.Embed`.

**`403` en `/private/login`** — Credenciales incorrectas o vencidas. Verificar `CLIENT_ID` y `CLIENT_SECRET`.

**Reporte en blanco sin error** — El contenedor HTML no tiene altura definida. Asegurarse de que tenga `height` explícito.
