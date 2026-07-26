# Configuración de Firebase (Firestore) — Robot Bípedo

La PC guarda cada factura en **SQLite local** (como siempre) y además la manda a
**Firestore** en la nube para poder consultarla desde cualquier lado. Si no hay
internet, la factura no se pierde: queda pendiente y se reintenta después.

Seguí estos pasos **una sola vez** para dejar la nube andando.

## 1. Crear el proyecto en Firebase

1. Entrá a https://console.firebase.google.com y hacé login con tu cuenta de Google.
2. Clic en **Agregar proyecto** → poné un nombre (ej. `robot-bipedo`) → seguí los pasos.
   Podés desactivar Google Analytics, no hace falta.

## 2. Crear la base de datos Firestore

1. En el menú izquierdo: **Compilación → Firestore Database**.
2. Clic en **Crear base de datos**.
3. Elegí **Modo de producción** (o de prueba si estás experimentando).
4. Elegí la ubicación más cercana (ej. `southamerica-east1`) y confirmá.

## 3. Descargar las credenciales (cuenta de servicio)

1. Clic en el engranaje ⚙ arriba a la izquierda → **Configuración del proyecto**.
2. Pestaña **Cuentas de servicio**.
3. Clic en **Generar nueva clave privada** → **Generar clave**.
4. Se descarga un archivo `.json`. **Renombralo a `firebase_credentials.json`**
   y guardalo dentro de la carpeta `pc/` de este proyecto.

> ⚠️ Ese archivo es una contraseña. **No lo subas a git ni lo compartas.**
> Ya está protegido en `.gitignore`.

## 4. Instalar la librería en la PC

```powershell
pip install firebase-admin
```

## 5. Activar Firebase en la configuración

Abrí `config.json` y en la sección `"firebase"` poné:

```json
"firebase": {
  "activo": true,
  "archivo_credenciales": "firebase_credentials.json",
  "coleccion_transacciones": "transacciones",
  "coleccion_clientes": "clientes"
}
```

## 6. Probar

Arrancá el servidor como siempre:

```powershell
cd pc
python pc_server.py
```

Si todo está bien vas a ver en consola:

```
[FIREBASE] Conectado — colección 'transacciones'.
```

Hacé una venta de prueba (cédula + QR). En la consola de Firebase, entrá a
**Firestore Database** y vas a ver la colección `transacciones` con el registro.

## Si no hay internet

El robot sigue funcionando normal y guarda todo en SQLite. Las facturas que no
se pudieron subir quedan marcadas como pendientes. Cuando vuelva la conexión,
corré:

```powershell
cd pc
python sync_firebase.py
```

y sube todo lo pendiente sin duplicar nada.

## ¿Cómo consultar los datos?

Por ahora tenés tres formas, de la más simple a la más elaborada:

1. **Consola de Firebase** (lo más rápido): entrás a Firestore Database y ves /
   filtrás las transacciones en el navegador. Sin programar nada.
2. **Dashboard local de la PC** (ya existe): `http://localhost:8000`, lee del
   SQLite local.
3. **Consulta propia en la nube** (a futuro, si hace falta): se puede armar una
   página o app que lea de Firestore. Queda para más adelante según lo definas.
