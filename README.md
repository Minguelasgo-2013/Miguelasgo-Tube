# 🎬 Miguelasgo Tube

Tu propio YouTube casero, corriendo desde un pen drive en tu red local.
Sin Firebase, sin tarjetas de crédito, sin nube — todo vive en tu pen drive
y se sirve a cualquier dispositivo conectado a tu misma red WiFi.

---

## 📁 Estructura del proyecto

```
miguelasgo-tube-local/
├── iniciar.sh           ← doble clic / "bash iniciar.sh" para arrancar
├── server.py             ← el servidor (alternativa manual)
├── data.json              ← se crea solo: vídeos y comentarios
├── usuarios.json          ← se crea solo: cuentas (usuario + hash, nunca la contraseña)
├── static/
│   └── index.html          ← el frontend (no lo abras directamente con doble clic)
├── videos/                 ← archivos de vídeo subidos
└── thumbs/                 ← miniaturas .jpg generadas automáticamente
```

---

## 📥 Cómo descargarlo desde GitHub

```bash
git clone https://github.com/TU_USUARIO/miguelasgo-tube.git
cd miguelasgo-tube
python3 server.py
```

> 📌 **Nota:** los vídeos (`videos/`), miniaturas (`thumbs/`) y datos
> generados (`data.json`, `usuarios.json`) **no se suben a GitHub** —
> están en `.gitignore` a propósito. Al clonar el repo, esas carpetas
> empiezan vacías. Esto significa que **las cuentas son propias de
> cada copia del proyecto** — si mueves el proyecto a otro pen drive
> sin copiar `usuarios.json`, las cuentas no viajan con él.

---

## 🚀 Cómo arrancarlo

### Requisitos

- **Python 3** (ya lo tienes en tu equipo).
- **No necesita pip, Flask, ni ningún paquete adicional** — todo es
  librería estándar de Python, así que funciona incluso en equipos
  restringidos (centros educativos, aulas Abalar) sin permisos de
  administrador.
- **ffmpeg (opcional, recomendado)** para miniaturas reales. Si no
  está, el navegador muestra una vista previa en directo en su lugar.
  Comprobar con: `which ffmpeg`.

### Si tu pen drive está en FAT32/exFAT

Los sistemas de archivos FAT no soportan permisos Unix, así que
`chmod +x` no sirve de nada ahí y `./iniciar.sh` puede dar
"Permiso denegado" aunque el archivo esté bien. Usa en su lugar:

```bash
bash iniciar.sh
```

### Opción A — Doble clic

1. Conecta el pen drive y entra en la carpeta `miguelasgo-tube-local`.
2. Doble clic en `iniciar.sh` → elige "Ejecutar" si tu gestor de
   archivos te lo pregunta.
3. Si no pasa nada (común en pen drives FAT32, o en Dolphin/KDE sin
   asociación configurada), abre una terminal en esa carpeta y ejecuta
   `bash iniciar.sh`.

### Opción B — Manual desde terminal

```bash
cd /media/usuario/TU_PEN/miguelasgo-tube-local
python3 server.py
```

Verás algo así:

```
========================================================
  🎬  MIGUELASGO TUBE - Servidor local
========================================================
  En este equipo:   http://localhost:8000
  En tu red local:  http://192.168.1.XX:8000
========================================================
  ✅ ffmpeg detectado: se generarán miniaturas reales.
  🔐 Sistema de cuentas activo (usuario + contraseña).
========================================================
```

- **En tu propio PC**: abre `http://localhost:8000`.
- **Desde el móvil u otro PC en la misma WiFi**: abre la segunda URL.

---

## 🔐 Sistema de cuentas (usuario + contraseña)

A diferencia de versiones anteriores de este proyecto, ahora **es
obligatorio crear una cuenta** para subir vídeos o comentar — como en
cualquier plataforma real.

**Cómo funciona por dentro:**

1. Al registrarte, escribes un usuario (3-24 letras/números/guion
   bajo) y una contraseña (mínimo 6 caracteres).
2. El servidor **nunca guarda tu contraseña tal cual**. En su lugar
   calcula un *hash* (con PBKDF2-HMAC-SHA256, 200.000 iteraciones, el
   mismo tipo de algoritmo recomendado por estándares de seguridad
   actuales) combinado con una "sal" aleatoria distinta para cada
   usuario. Ese hash es matemáticamente imposible de revertir para
   recuperar la contraseña original — incluso si alguien copiara el
   archivo `usuarios.json` entero, no podría leer ninguna contraseña.
3. Al iniciar sesión, el servidor te entrega un **token de sesión**
   (un código aleatorio largo) que tu navegador guarda y reutiliza en
   cada petición protegida, durante 30 días o hasta que cierres sesión.
4. Solo el usuario que subió un vídeo o escribió un comentario puede
   borrarlo — el servidor comprueba que tu sesión coincide con el
   autor original antes de permitir el borrado.
5. Hay un límite de 8 intentos de login fallidos cada 5 minutos por
   dispositivo, para dificultar adivinar contraseñas por fuerza bruta.

**Importante:** ahora que las cuentas viven en `usuarios.json` y son
propias de tu pen drive, **haz copia de seguridad de ese archivo** si
no quieres arriesgarte a perder el acceso a tu cuenta si el pen se
estropea. El archivo solo contiene usuarios y hashes, nunca
contraseñas en claro, así que aunque alguien lo vea no puede usarlo
para iniciar sesión como tú.

---

## 🛠️ Funciones incluidas

- **Sistema de cuentas real**: registro, login, logout, sesiones
- Contraseñas con hash seguro (nunca en texto plano)
- Subida real de archivos de vídeo (con barra de progreso)
- Miniaturas reales generadas con ffmpeg
- Grid de vídeos estilo YouTube, buscador y filtro por categorías
- Reproductor con contador de visitas y likes
- Comentarios por vídeo (solo borrables por su autor)
- Perfil de canal: clic en cualquier autor para ver sus vídeos y stats
- **Solo el dueño de la cuenta puede eliminar sus propios vídeos y comentarios**
- Protección anti-spam: máximo 5 subidas de vídeo por IP cada 10 min,
  y máximo 8 intentos de login fallidos cada 5 min
- Tema claro/oscuro
- Indicador de "servidor conectado/desconectado"

---

## 🔧 Configuración avanzada

Abre `server.py` y modifica estas constantes cerca del principio:

```python
LIMITE_TAMANO_MB = 500                      # tamaño máximo por vídeo
PUERTO = 8000                                # puerto del servidor
LIMITE_SUBIDAS_POR_IP = 5                    # subidas máximas por IP...
VENTANA_SUBIDAS_SEGUNDOS = 600                # ...cada X segundos
LIMITE_INTENTOS_LOGIN = 8                    # intentos de login...
VENTANA_INTENTOS_LOGIN_SEGUNDOS = 300         # ...cada X segundos
DURACION_SESION_SEGUNDOS = 30 * 24 * 60 * 60  # 30 días de sesión
USUARIO_MIN_LEN = 3                          # longitud mínima de usuario
USUARIO_MAX_LEN = 24
PASSWORD_MIN_LEN = 6                         # longitud mínima de contraseña
```

---

## ⚠️ Cosas importantes que debes saber

- **El servidor debe estar encendido** para que los demás puedan ver
  la página. Si lo apagas, deja de funcionar para todos.
- **La IP local puede cambiar** al conectarte a una red distinta.
- **Formatos de vídeo permitidos**: mp4, webm, mov, mkv, avi, ogg.
- Si tienes un **firewall activo**, puede que tengas que permitir
  conexiones entrantes al puerto 8000.
- Este sistema de cuentas es robusto para un proyecto de aula o entre
  amigos, pero no sustituye medidas adicionales (verificación por
  email, recuperación de contraseña, etc.) que tendría un servicio en
  producción real a gran escala.

---

## 💾 ¿Dónde están mis datos?

- **Vídeos**: `videos/` · **Miniaturas**: `thumbs/`
- **Vídeos/comentarios (metadatos)**: `data.json`
- **Cuentas (usuario + hash + sesiones activas)**: `usuarios.json`

Como todo vive en el pen drive, puedes llevártelo a cualquier otro PC
con Python instalado y seguirá funcionando igual — cuentas incluidas.
