#!/usr/bin/env python3
"""
Miguelasgo Tube - Servidor local (modificado con nuevas características):
- dislikes, tags, visibilidad (privado/publico)
- suscripciones + feed
- notificaciones para suscriptores tras subida
- historial de reproducción por usuario
- reportes para moderación
- endpoints adicionales: suscripcion, notificaciones, historial, report
"""

import hashlib, hmac, json, mimetypes, re, secrets, shutil, socket, subprocess, time, uuid, os
from collections import defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote

# CONFIG
BASE_DIR = Path(__file__).resolve().parent
VIDEOS_DIR = BASE_DIR / "videos"
THUMBS_DIR = BASE_DIR / "thumbs"
STATIC_DIR = BASE_DIR / "static"
DATA_FILE = BASE_DIR / "data.json"
USUARIOS_FILE = BASE_DIR / "usuarios.json"

EXTENSIONES_PERMITIDAS = {".mp4", ".webm", ".mov", ".mkv", ".avi", ".ogg"}
LIMITE_TAMANO_MB = 500
PUERTO = 8000

LIMITE_SUBIDAS_POR_IP = 5
VENTANA_SUBIDAS_SEGUNDOS = 600  # 10 minutos
LIMITE_INTENTOS_LOGIN = 8
VENTANA_INTENTOS_LOGIN_SEGUNDOS = 300  # 5 minutos
DURACION_SESION_SEGUNDOS = 30 * 24 * 60 * 60  # 30 días

USUARIO_MIN_LEN = 3
USUARIO_MAX_LEN = 24
PASSWORD_MIN_LEN = 6

FFMPEG_DISPONIBLE = shutil.which("ffmpeg") is not None

VIDEOS_DIR.mkdir(exist_ok=True)
THUMBS_DIR.mkdir(exist_ok=True)
STATIC_DIR.mkdir(exist_ok=True)

_subidas_recientes = defaultdict(list)
_intentos_login = defaultdict(list)

PBKDF2_ITERACIONES = 200_000
MAX_NOTIFICACIONES_POR_USUARIO = 100

# UTIL: cargar/guardar datos
def cargar_datos():
    if not DATA_FILE.exists():
        return {"videos": [], "comentarios": {}, "reportes": []}
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            datos = json.load(f)
            datos.setdefault("videos", [])
            datos.setdefault("comentarios", {})
            datos.setdefault("reportes", [])
            return datos
    except (json.JSONDecodeError, OSError):
        return {"videos": [], "comentarios": {}, "reportes": []}

def guardar_datos(datos):
    tmp = DATA_FILE.with_suffix(".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(datos, f, ensure_ascii=False, indent=2)
        tmp.replace(DATA_FILE)
    except Exception as e:
        if tmp.exists():
            try: tmp.unlink()
            except: pass
        raise e

def cargar_usuarios():
    if not USUARIOS_FILE.exists():
        return {"usuarios": {}, "sesiones": {}}
    try:
        with open(USUARIOS_FILE, "r", encoding="utf-8") as f:
            datos = json.load(f)
            datos.setdefault("usuarios", {})
            datos.setdefault("sesiones", {})
            # normalizar usuarios entries: asegurarse de campos adicionales
            for u, info in list(datos.get("usuarios", {}).items()):
                info.setdefault("suscripciones", [])
                info.setdefault("suscriptores", [])
                info.setdefault("notificaciones", [])
                info.setdefault("historial", [])
            return datos
    except (json.JSONDecodeError, OSError):
        return {"usuarios": {}, "sesiones": {}}

def guardar_usuarios(datos):
    tmp = USUARIOS_FILE.with_suffix(".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(datos, f, ensure_ascii=False, indent=2)
        tmp.replace(USUARIOS_FILE)
    except Exception as e:
        if tmp.exists():
            try: tmp.unlink()
            except: pass
        raise e

def obtener_ip_local():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except OSError:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip

def generar_hash_password(password, sal=None):
    if sal is None:
        sal = secrets.token_hex(16)
    derivado = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), sal.encode("utf-8"), PBKDF2_ITERACIONES)
    return sal, derivado.hex()

def verificar_password(password, sal, hash_guardado):
    _, hash_calculado = generar_hash_password(password, sal)
    return hmac.compare_digest(hash_calculado, hash_guardado)

def es_nombre_usuario_valido(nombre):
    return bool(re.match(r'^[a-zA-Z0-9_]{%d,%d}$' % (USUARIO_MIN_LEN, USUARIO_MAX_LEN), nombre))

def generar_token():
    return secrets.token_hex(24)

def crear_sesion(nombre_usuario):
    token = generar_token()
    datos = cargar_usuarios()
    datos.setdefault("sesiones", {})[token] = {
        "usuario": nombre_usuario,
        "expira": time.time() + DURACION_SESION_SEGUNDOS,
    }
    ahora = time.time()
    datos["sesiones"] = { t:s for t,s in datos["sesiones"].items() if s.get("expira",0) > ahora }
    guardar_usuarios(datos)
    return token

def usuario_de_sesion(token):
    if not token:
        return None
    datos = cargar_usuarios()
    sesion = datos.get("sesiones", {}).get(token)
    if not sesion:
        return None
    if sesion.get("expira",0) < time.time():
        return None
    return sesion.get("usuario")

def cerrar_sesion(token):
    datos = cargar_usuarios()
    datos.get("sesiones", {}).pop(token, None)
    guardar_usuarios(datos)

def comprobar_limite_subidas(ip):
    ahora = time.time()
    recientes = _subidas_recientes[ip]
    recientes[:] = [t for t in recientes if ahora - t < VENTANA_SUBIDAS_SEGUNDOS]
    return len(recientes) < LIMITE_SUBIDAS_POR_IP

def registrar_subida(ip):
    _subidas_recientes[ip].append(time.time())

def comprobar_limite_login(ip):
    ahora = time.time()
    recientes = _intentos_login[ip]
    recientes[:] = [t for t in recientes if ahora - t < VENTANA_INTENTOS_LOGIN_SEGUNDOS]
    return len(recientes) < LIMITE_INTENTOS_LOGIN

def registrar_intento_login(ip):
    _intentos_login[ip].append(time.time())

def es_nombre_archivo_seguro(nombre):
    return bool(re.match(r'^[a-zA-Z0-9_\-]+\.[a-zA-Z0-9]+$', nombre))

def generar_miniatura(ruta_video, video_id):
    if not FFMPEG_DISPONIBLE:
        return None
    ruta_thumb = THUMBS_DIR / f"{video_id}.jpg"
    try:
        resultado = subprocess.run(
            ["ffmpeg", "-y", "-ss", "1", "-i", str(ruta_video),
             "-frames:v", "1", "-vf", "scale=480:-1", "-q:v", "4", str(ruta_thumb)],
            capture_output=True, timeout=20,
        )
        if resultado.returncode == 0 and ruta_thumb.exists() and ruta_thumb.stat().st_size > 0:
            return ruta_thumb.name
        resultado2 = subprocess.run(
            ["ffmpeg", "-y", "-i", str(ruta_video),
             "-frames:v", "1", "-vf", "scale=480:-1", "-q:v", "4", str(ruta_thumb)],
            capture_output=True, timeout=20,
        )
        if resultado2.returncode == 0 and ruta_thumb.exists() and ruta_thumb.stat().st_size > 0:
            return ruta_thumb.name
        return None
    except (subprocess.TimeoutExpired, OSError):
        return None

class TubeHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, formato, *args):
        print(f"  {self.address_string()} - {formato % args}")

    def enviar_json(self, datos, status=200):
        cuerpo = json.dumps(datos, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(cuerpo)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(cuerpo)

    def enviar_error_json(self, mensaje, status=400):
        self.enviar_json({"error": mensaje}, status=status)

    def leer_json_body(self):
        longitud = int(self.headers.get("Content-Length", 0))
        if longitud <= 0:
            return {}
        cuerpo = self.rfile.read(longitud)
        try:
            return json.loads(cuerpo)
        except (json.JSONDecodeError, TypeError):
            return {}

    def obtener_token_sesion(self):
        auth = self.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            return auth[len("Bearer "):].strip()
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        return (query.get("sesion", [""])[0]).strip()

    def usuario_autenticado(self):
        return usuario_de_sesion(self.obtener_token_sesion())

    def enviar_archivo(self, ruta, content_type=None, descargar_nombre=None):
        if not ruta.exists() or not ruta.is_file():
            self.enviar_error_json("Archivo no encontrado.", 404)
            return
        tamano_total = ruta.stat().st_size
        if content_type is None:
            content_type, _ = mimetypes.guess_type(str(ruta))
            content_type = content_type or "application/octet-stream"
        rango = self.headers.get("Range")
        if rango and rango.startswith("bytes="):
            try:
                inicio_str, fin_str = rango.replace("bytes=", "").split("-")
                inicio = int(inicio_str) if inicio_str else 0
                fin = int(fin_str) if fin_str else tamano_total - 1
                fin = min(fin, tamano_total - 1)
                longitud = fin - inicio + 1
                self.send_response(206)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Range", f"bytes {inicio}-{fin}/{tamano_total}")
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Length", str(longitud))
                self.end_headers()
                with open(ruta, "rb") as f:
                    f.seek(inicio)
                    restante = longitud
                    bloque = 1024 * 256
                    while restante > 0:
                        leido = f.read(min(bloque, restante))
                        if not leido:
                            break
                        self.wfile.write(leido)
                        restante -= len(leido)
                return
            except (BrokenPipeError, ConnectionResetError):
                return
            except Exception:
                pass
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(tamano_total))
        if descargar_nombre:
            self.send_header("Content-Disposition", f'inline; filename="{descargar_nombre}"')
        self.end_headers()
        try:
            with open(ruta, "rb") as f:
                while True:
                    bloque = f.read(1024 * 256)
                    if not bloque:
                        break
                    self.wfile.write(bloque)
        except (BrokenPipeError, ConnectionResetError):
            pass

    # GET
    def do_GET(self):
        parsed = urlparse(self.path)
        ruta = unquote(parsed.path)
        query = parse_qs(parsed.query)
        try:
            if ruta == "/" or ruta == "":
                self.enviar_archivo(STATIC_DIR / "index.html", "text/html; charset=utf-8")
                return
            if ruta.startswith("/static/"):
                nombre = ruta[len("/static/"):]
                if "/" in nombre and not es_nombre_archivo_seguro(nombre.replace("/", "_")):
                    self.enviar_error_json("Ruta no válida.", 400)
                    return
                self.enviar_archivo(STATIC_DIR / nombre)
                return
            if ruta.startswith("/videos/"):
                nombre = ruta[len("/videos/"):]
                if not es_nombre_archivo_seguro(nombre):
                    self.enviar_error_json("Nombre de archivo no válido.", 400)
                    return
                self.enviar_archivo(VIDEOS_DIR / nombre)
                return
            if ruta.startswith("/thumbs/"):
                nombre = ruta[len("/thumbs/"):]
                if not es_nombre_archivo_seguro(nombre):
                    self.enviar_error_json("Nombre de archivo no válido.", 400)
                    return
                self.enviar_archivo(THUMBS_DIR / nombre)
                return

            if ruta == "/api/auth/yo":
                usuario = self.usuario_autenticado()
                if not usuario:
                    self.enviar_error_json("No has iniciado sesión.", 401)
                    return
                self.enviar_json({"usuario": usuario})
                return

            if ruta == "/api/videos":
                datos = cargar_datos()
                videos = [self._video_publico(v) for v in datos.get("videos", [])]
                busqueda = (query.get("q", [""])[0]).strip().lower()
                categoria = (query.get("categoria", [""])[0]).strip()
                autor = (query.get("autor", [""])[0]).strip()
                tag = (query.get("tag", [""])[0]).strip()
                usuario = self.usuario_autenticado()

                resultado = []
                for v in videos:
                    # visibilidad: publico o privado (privados solo autor o suscriptores)
                    if v.get("visibilidad","publico") == "privado":
                        if not usuario:
                            continue
                        if usuario != v.get("autor"):
                            # comprobar si usuario está suscrito
                            udata = cargar_usuarios()
                            userinfo = udata.get("usuarios", {}).get(usuario, {})
                            if v.get("autor") not in userinfo.get("suscripciones", []):
                                continue
                    resultado.append(v)

                if busqueda:
                    resultado = [vv for vv in resultado if busqueda in (vv.get("titulo","").lower()) or busqueda in (vv.get("descripcion","").lower())]
                if categoria and categoria.lower() != "todos":
                    resultado = [vv for vv in resultado if vv.get("categoria") == categoria]
                if autor:
                    resultado = [vv for vv in resultado if vv.get("autor") == autor]
                if tag:
                    resultado = [vv for vv in resultado if tag in (vv.get("tags") or [])]

                resultado = sorted(resultado, key=lambda v: v.get("fecha", 0), reverse=True)
                self.enviar_json(resultado)
                return

            m = re.match(r'^/api/videos/([a-zA-Z0-9]+)$', ruta)
            if m:
                video = self._buscar_video(m.group(1))
                if not video:
                    self.enviar_error_json("Vídeo no encontrado.", 404)
                    return
                # visibilidad check
                if video.get("visibilidad","publico") == "privado":
                    usuario = self.usuario_autenticado()
                    if not usuario:
                        self.enviar_error_json("Vídeo privado.", 403)
                        return
                    if usuario != video.get("autor"):
                        udata = cargar_usuarios()
                        userinfo = udata.get("usuarios", {}).get(usuario, {})
                        if video.get("autor") not in userinfo.get("suscripciones", []):
                            self.enviar_error_json("Vídeo privado.", 403)
                            return
                self.enviar_json(self._video_publico(video))
                return

            m = re.match(r'^/api/videos/([a-zA-Z0-9]+)/comentarios$', ruta)
            if m:
                video_id = m.group(1)
                datos = cargar_datos()
                comentarios = datos.get("comentarios", {}).get(video_id, [])
                self.enviar_json(sorted(comentarios, key=lambda c: c.get("fecha", 0)))
                return

            m = re.match(r'^/api/canal/([^/]+)$', ruta)
            if m:
                nombre_canal = unquote(m.group(1))
                datos = cargar_datos()
                videos_canal = [self._video_publico(v) for v in datos.get("videos", []) if v.get("autor") == nombre_canal]
                # filtrar privados: solo mostrar a quien corresponda si hay sesion param
                usuario = self.usuario_autenticado()
                resultado = []
                for v in videos_canal:
                    if v.get("visibilidad","publico") == "privado":
                        if not usuario:
                            continue
                        if usuario != v.get("autor"):
                            udata = cargar_usuarios()
                            userinfo = udata.get("usuarios", {}).get(usuario, {})
                            if nombre_canal not in userinfo.get("suscripciones", []):
                                continue
                    resultado.append(v)
                total_vistas = sum(v.get("vistas", 0) for v in resultado)
                total_likes = sum(v.get("likes", 0) for v in resultado)
                resultado = sorted(resultado, key=lambda v: v.get("fecha", 0), reverse=True)
                self.enviar_json({
                    "autor": nombre_canal,
                    "num_videos": len(resultado),
                    "total_vistas": total_vistas,
                    "total_likes": total_likes,
                    "videos": resultado,
                })
                return

            # estado suscripcion
            if ruta == "/api/suscripcion/estado":
                canal = (query.get("canal", [""])[0]).strip()
                usuario = self.usuario_autenticado()
                if not canal:
                    self.enviar_error_json("Falta canal.", 400)
                    return
                sus = False
                if usuario:
                    datos = cargar_usuarios()
                    us = datos.get("usuarios", {}).get(usuario, {})
                    sus = canal in us.get("suscripciones", [])
                self.enviar_json({"suscrito": sus})
                return

            # feed suscripciones
            if ruta == "/api/suscripciones/feed":
                usuario = self.usuario_autenticado()
                if not usuario:
                    self.enviar_error_json("Debes iniciar sesión para ver el feed.", 401)
                    return
                datos = cargar_usuarios()
                us = datos.get("usuarios", {}).get(usuario, {})
                subs = us.get("suscripciones", [])
                datos_v = cargar_datos()
                videos = []
                for v in datos_v.get("videos", []):
                    if v.get("autor") in subs:
                        videos.append(self._video_publico(v))
                videos = sorted(videos, key=lambda v: v.get("fecha",0), reverse=True)
                self.enviar_json(videos)
                return

            # notificaciones (lista)
            if ruta == "/api/notificaciones":
                usuario = self.usuario_autenticado()
                if not usuario:
                    self.enviar_error_json("Debes iniciar sesión.", 401); return
                datos = cargar_usuarios()
                us = datos.get("usuarios", {}).get(usuario, {})
                notis = sorted(us.get("notificaciones", []), key=lambda n: n.get("fecha",0), reverse=True)
                self.enviar_json(notis)
                return

            if ruta == "/api/notificaciones/pendientes":
                usuario = self.usuario_autenticado()
                if not usuario:
                    self.enviar_error_json("Debes iniciar sesión.", 401); return
                datos = cargar_usuarios()
                us = datos.get("usuarios", {}).get(usuario, {})
                pendientes = sum(1 for n in us.get("notificaciones", []) if not n.get("leido"))
                self.enviar_json({"pendientes": pendientes})
                return

            # historial: devolvemos videos con metadatos y posicion guardada
            if ruta == "/api/historial":
                usuario = self.usuario_autenticado()
                if not usuario:
                    self.enviar_error_json("Debes iniciar sesión.", 401); return
                datos = cargar_usuarios()
                us = datos.get("usuarios", {}).get(usuario, {})
                historial = []
                datos_v = cargar_datos()
                for h in sorted(us.get("historial", []), key=lambda x: x.get("fecha",0), reverse=True):
                    vid = next((v for v in datos_v.get("videos",[]) if v["id"]==h.get("video")), None)
                    if vid:
                        historial.append({"video": self._video_publico(vid), "fecha": h.get("fecha"), "posicion": h.get("posicion",0)})
                self.enviar_json(historial)
                return

            self.enviar_error_json("Ruta no encontrada.", 404)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            try: self.enviar_error_json(f"Error interno: {e}", 500)
            except: pass

    @staticmethod
    def _video_publico(video):
        # copiado excepto token y datos internos
        # we ensure tags, visibilidad y dislikes existen
        v = {k: val for k, val in video.items() if k != "token"}
        v.setdefault("tags", video.get("tags", []))
        v.setdefault("visibilidad", video.get("visibilidad", "publico"))
        v.setdefault("dislikes", video.get("dislikes", 0))
        return v

    @staticmethod
    def _buscar_video(video_id, datos=None):
        datos = datos or cargar_datos()
        return next((v for v in datos.get("videos", []) if v["id"] == video_id), None)

    # POST
    def do_POST(self):
        parsed = urlparse(self.path)
        ruta = unquote(parsed.path)

        try:
            if ruta == "/api/auth/registro":
                self.manejar_registro(); return
            if ruta == "/api/auth/login":
                self.manejar_login(); return
            if ruta == "/api/auth/logout":
                token = self.obtener_token_sesion()
                if token: cerrar_sesion(token)
                self.enviar_json({"ok": True}); return
            if ruta == "/api/videos":
                self.manejar_subida_video(); return

            m = re.match(r'^/api/videos/([a-zA-Z0-9]+)/vista$', ruta)
            if m:
                datos = cargar_datos()
                video = self._buscar_video(m.group(1), datos)
                if not video:
                    self.enviar_error_json("Vídeo no encontrado.", 404); return
                video["vistas"] = video.get("vistas", 0) + 1
                # si viene sesion, guardamos historial del usuario
                usuario = self.usuario_autenticado()
                if usuario:
                    datos_u = cargar_usuarios()
                    uinfo = datos_u.get("usuarios", {}).get(usuario, {})
                    lista = uinfo.setdefault("historial", [])
                    lista.append({"video": video["id"], "fecha": time.time(), "posicion": 0})
                    # limitar a últimas 500 entradas
                    uinfo["historial"] = lista[-500:]
                    guardar_usuarios(datos_u)
                guardar_datos(datos)
                self.enviar_json({"vistas": video["vistas"]}); return

            m = re.match(r'^/api/videos/([a-zA-Z0-9]+)/like$', ruta)
            if m:
                datos = cargar_datos()
                video = self._buscar_video(m.group(1), datos)
                if not video:
                    self.enviar_error_json("Vídeo no encontrado.", 404); return
                payload = self.leer_json_body()
                try: incremento = int(payload.get("incremento", 1))
                except: incremento = 1
                video["likes"] = max(0, video.get("likes", 0) + incremento)
                guardar_datos(datos)
                self.enviar_json({"likes": video["likes"]}); return

            m = re.match(r'^/api/videos/([a-zA-Z0-9]+)/dislike$', ruta)
            if m:
                datos = cargar_datos()
                video = self._buscar_video(m.group(1), datos)
                if not video:
                    self.enviar_error_json("Vídeo no encontrado.", 404); return
                payload = self.leer_json_body()
                try: incremento = int(payload.get("incremento", 1))
                except: incremento = 1
                video["dislikes"] = max(0, video.get("dislikes", 0) + incremento)
                guardar_datos(datos)
                self.enviar_json({"dislikes": video["dislikes"]}); return

            m = re.match(r'^/api/videos/([a-zA-Z0-9]+)/comentarios$', ruta)
            if m:
                video_id = m.group(1)
                usuario = self.usuario_autenticado()
                if not usuario:
                    self.enviar_error_json("Debes iniciar sesión para comentar.", 401); return
                datos = cargar_datos()
                video = self._buscar_video(video_id, datos)
                if not video:
                    self.enviar_error_json("Vídeo no encontrado.", 404); return
                payload = self.leer_json_body()
                texto = (payload.get("texto") or "").strip()[:500]
                if not texto:
                    self.enviar_error_json("Escribe un comentario.", 400); return
                comentario = {"id": uuid.uuid4().hex[:12], "apodo": usuario, "texto": texto, "fecha": time.time()}
                datos.setdefault("comentarios", {}).setdefault(video_id, []).append(comentario)
                guardar_datos(datos)
                self.enviar_json(comentario, status=201); return

            if ruta == "/api/suscripcion":
                usuario = self.usuario_autenticado()
                if not usuario:
                    self.enviar_error_json("Debes iniciar sesión.", 401); return
                payload = self.leer_json_body()
                canal = (payload.get("canal") or "").strip()
                accion = (payload.get("accion") or "").strip()
                if not canal or accion not in ("subscribe", "unsubscribe"):
                    self.enviar_error_json("Parámetros inválidos.", 400); return
                datos = cargar_usuarios()
                usuarios = datos.setdefault("usuarios", {})
                us = usuarios.setdefault(usuario, {})
                us.setdefault("suscripciones", [])
                target = usuarios.setdefault(canal, {})
                target.setdefault("suscriptores", [])
                if accion == "subscribe":
                    if canal not in us["suscripciones"]:
                        us["suscripciones"].append(canal)
                    if usuario not in target["suscriptores"]:
                        target["suscriptores"].append(usuario)
                else:
                    us["suscripciones"] = [x for x in us.get("suscripciones",[]) if x!=canal]
                    target["suscriptores"] = [x for x in target.get("suscriptores",[]) if x!=usuario]
                guardar_usuarios(datos)
                self.enviar_json({"suscrito": canal in us.get("suscripciones", [])}); return

            if ruta == "/api/notificaciones/leer":
                usuario = self.usuario_autenticado()
                if not usuario:
                    self.enviar_error_json("Debes iniciar sesión.", 401); return
                datos = cargar_usuarios()
                u = datos.get("usuarios", {}).get(usuario, {})
                for n in u.get("notificaciones", []): n["leido"] = True
                guardar_usuarios(datos)
                self.enviar_json({"ok": True}); return

            if ruta == "/api/report":
                payload = self.leer_json_body()
                vid = payload.get("video")
                motivo = payload.get("motivo")
                comentario = payload.get("comentario", "")
                reporter = self.usuario_autenticado()
                if not vid or not motivo:
                    self.enviar_error_json("Faltan campos.", 400); return
                datos = cargar_datos()
                reporte = {"id": uuid.uuid4().hex[:12], "video": vid, "motivo": motivo, "comentario": comentario, "reporter": reporter, "fecha": time.time()}
                datos.setdefault("reportes", []).append(reporte)
                guardar_datos(datos)
                self.enviar_json({"ok": True}, status=201); return

            if ruta == "/api/historial/posicion":
                # método beacon para guardar posición parcial (JSON en body)
                payload = self.leer_json_body()
                video = payload.get("video")
                posicion = int(payload.get("posicion") or 0)
                usuario = usuario_de_sesion(payload.get("sesion") or self.obtener_token_sesion())
                if usuario:
                    datos = cargar_usuarios()
                    us = datos.get("usuarios", {}).get(usuario, {})
                    lista = us.setdefault("historial", [])
                    lista.append({"video": video, "fecha": time.time(), "posicion": posicion})
                    us["historial"] = lista[-500:]
                    guardar_usuarios(datos)
                    self.enviar_json({"ok": True}); return
                else:
                    self.send_response(204); self.end_headers(); return

            self.enviar_error_json("Ruta no encontrada.", 404)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            try: self.enviar_error_json(f"Error interno: {e}", 500)
            except: pass

    def manejar_registro(self):
        ip_cliente = self.client_address[0]
        if not comprobar_limite_login(ip_cliente):
            self.enviar_error_json("Demasiados intentos. Espera unos minutos.", 429); return
        registrar_intento_login(ip_cliente)
        payload = self.leer_json_body()
        usuario = (payload.get("usuario") or "").strip()
        password = payload.get("password") or ""
        if not es_nombre_usuario_valido(usuario):
            self.enviar_error_json(f"El nombre de usuario debe tener entre {USUARIO_MIN_LEN} y {USUARIO_MAX_LEN} caracteres, y solo letras, números o guion bajo.", 400); return
        if len(password) < PASSWORD_MIN_LEN:
            self.enviar_error_json(f"La contraseña debe tener al menos {PASSWORD_MIN_LEN} caracteres.", 400); return
        datos = cargar_usuarios()
        usuario_lower = usuario.lower()
        if usuario_lower in {u.lower() for u in datos.get("usuarios", {})}:
            self.enviar_error_json("Ese nombre de usuario ya está en uso.", 409); return
        sal, hash_pw = generar_hash_password(password)
        datos.setdefault("usuarios", {})[usuario] = {"sal": sal, "hash": hash_pw, "fecha_registro": time.time(), "suscripciones": [], "suscriptores": [], "notificaciones": [], "historial": []}
        guardar_usuarios(datos)
        token_sesion = crear_sesion(usuario)
        self.enviar_json({"usuario": usuario, "sesion": token_sesion}, status=201)

    def manejar_login(self):
        ip_cliente = self.client_address[0]
        if not comprobar_limite_login(ip_cliente):
            self.enviar_error_json("Demasiados intentos de inicio de sesión. Espera unos minutos.", 429); return
        registrar_intento_login(ip_cliente)
        payload = self.leer_json_body()
        usuario = (payload.get("usuario") or "").strip()
        password = payload.get("password") or ""
        datos = cargar_usuarios()
        registro = None
        nombre_real = None
        for u, info in datos.get("usuarios", {}).items():
            if u.lower() == usuario.lower():
                registro = info
                nombre_real = u
                break
        credenciales_invalidas = "Usuario o contraseña incorrectos."
        if not registro:
            self.enviar_error_json(credenciales_invalidas, 401); return
        if not verificar_password(password, registro["sal"], registro["hash"]):
            self.enviar_error_json(credenciales_invalidas, 401); return
        token_sesion = crear_sesion(nombre_real)
        self.enviar_json({"usuario": nombre_real, "sesion": token_sesion})

    def manejar_subida_video(self):
        ip_cliente = self.client_address[0]
        if not comprobar_limite_subidas(ip_cliente):
            minutos = VENTANA_SUBIDAS_SEGUNDOS // 60
            self.enviar_error_json(f"Has subido demasiados vídeos seguidos. Espera unos minutos antes de subir otro (máximo {LIMITE_SUBIDAS_POR_IP} cada {minutos} min).", 429); return
        usuario = self.usuario_autenticado()
        if not usuario:
            longitud = int(self.headers.get("Content-Length", 0))
            if longitud > 0: self.rfile.read(longitud)
            self.enviar_error_json("Debes iniciar sesión para subir vídeos.", 401); return
        content_type = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in content_type:
            self.enviar_error_json("Se esperaba multipart/form-data.", 400); return
        m = re.search(r'boundary=("?)([^">;]+)\1', content_type)
        if not m:
            self.enviar_error_json("Petición multipart mal formada (sin boundary).", 400); return
        boundary = m.group(2).encode("utf-8")
        longitud = int(self.headers.get("Content-Length", 0))
        limite_bytes = LIMITE_TAMANO_MB * 1024 * 1024
        if longitud > limite_bytes:
            self.rfile.read(longitud)
            self.enviar_error_json(f"El archivo supera el límite de {LIMITE_TAMANO_MB}MB.", 413); return
        if longitud <= 0:
            self.enviar_error_json("Cuerpo de la petición vacío.", 400); return
        cuerpo = self.rfile.read(longitud)
        try:
            campos, archivo_info = self._parsear_multipart(cuerpo, boundary)
        except ValueError as e:
            self.enviar_error_json(f"Error al leer el archivo: {e}", 400); return
        titulo = (campos.get("titulo") or "").strip()[:100]
        descripcion = (campos.get("descripcion") or "").strip()[:500]
        categoria = (campos.get("categoria") or "Otros").strip() or "Otros"
        tags_raw = (campos.get("tags") or "").strip()
        visibilidad = (campos.get("visibilidad") or "publico").strip()
        if not titulo:
            self.enviar_error_json("El título es obligatorio.", 400); return
        if not archivo_info or not archivo_info.get("filename"):
            self.enviar_error_json("No se envió ningún archivo.", 400); return
        nombre_original = archivo_info["filename"]
        extension = Path(nombre_original).suffix.lower()
        if extension not in EXTENSIONES_PERMITIDAS:
            self.enviar_error_json(f"Formato no permitido. Usa: {', '.join(sorted(EXTENSIONES_PERMITIDAS))}", 400); return
        video_id = uuid.uuid4().hex[:12]
        nombre_archivo = f"{video_id}{extension}"
        ruta_destino = VIDEOS_DIR / nombre_archivo
        with open(ruta_destino, "wb") as f:
            f.write(archivo_info["data"])
        tamano_mb = round(ruta_destino.stat().st_size / 1024 / 1024, 2)
        nombre_thumb = generar_miniatura(ruta_destino, video_id)
        tags = [t.strip() for t in tags_raw.split(",") if t.strip()] if tags_raw else []
        nuevo_video = {
            "id": video_id,
            "titulo": titulo,
            "descripcion": descripcion,
            "categoria": categoria,
            "autor": usuario,
            "archivo": nombre_archivo,
            "miniatura": nombre_thumb,
            "tamano_mb": tamano_mb,
            "fecha": time.time(),
            "vistas": 0,
            "likes": 0,
            "dislikes": 0,
            "tags": tags,
            "visibilidad": "privado" if visibilidad=="privado" else "publico",
        }
        datos = cargar_datos()
        datos.setdefault("videos", []).append(nuevo_video)
        guardar_datos(datos)
        registrar_subida(ip_cliente)
        # notificar a suscriptores
        usuarios = cargar_usuarios()
        for u_name, u_info in usuarios.get("usuarios", {}).items():
            if nuevo_video["autor"] in u_info.get("suscripciones", []):
                noti = {"id": uuid.uuid4().hex[:12], "titulo": f"Nuevo vídeo de {nuevo_video['autor']}", "texto": nuevo_video['titulo'], "fecha": time.time(), "leido": False}
                notificaciones = u_info.setdefault("notificaciones", [])
                notificaciones.append(noti)
                # limitar a MAX_NOTIFICACIONES_POR_USUARIO
                if len(notificaciones) > MAX_NOTIFICACIONES_POR_USUARIO:
                    u_info["notificaciones"] = notificaciones[-MAX_NOTIFICACIONES_POR_USUARIO:]
        guardar_usuarios(usuarios)
        self.enviar_json(self._video_publico(nuevo_video), status=201)

    @staticmethod
    def _parsear_multipart(cuerpo, boundary):
        delimitador = b"--" + boundary
        partes = cuerpo.split(delimitador)
        campos = {}
        archivo_info = None
        for parte in partes:
            parte = parte.strip(b"\r\n")
            if not parte or parte == b"--":
                continue
            if b"\r\n\r\n" not in parte:
                continue
            cabeceras_raw, contenido = parte.split(b"\r\n\r\n", 1)
            if contenido.endswith(b"\r\n"):
                contenido = contenido[:-2]
            cabeceras_texto = cabeceras_raw.decode("utf-8", errors="replace")
            disposicion = None
            for linea in cabeceras_texto.split("\r\n"):
                if linea.lower().startswith("content-disposition:"):
                    disposicion = linea
                    break
            if not disposicion:
                continue
            nombre_m = re.search(r'name="([^"]*)"', disposicion)
            filename_m = re.search(r'filename="([^"]*)"', disposicion)
            nombre_campo = nombre_m.group(1) if nombre_m else None
            if filename_m:
                archivo_info = {"filename": filename_m.group(1), "data": contenido}
            elif nombre_campo:
                campos[nombre_campo] = contenido.decode("utf-8", errors="replace")
        return campos, archivo_info

    # DELETE
    def do_DELETE(self):
        parsed = urlparse(self.path)
        ruta = unquote(parsed.path)
        try:
            m = re.match(r'^/api/videos/([a-zA-Z0-9]+)$', ruta)
            if m:
                video_id = m.group(1)
                usuario = self.usuario_autenticado()
                if not usuario:
                    self.enviar_error_json("Debes iniciar sesión.", 401); return
                datos = cargar_datos()
                videos = datos.get("videos", [])
                video = next((v for v in videos if v["id"] == video_id), None)
                if not video:
                    self.enviar_error_json("Vídeo no encontrado.", 404); return
                if video.get("autor") != usuario:
                    self.enviar_error_json("No tienes permiso para eliminar este vídeo (solo el autor original puede hacerlo).", 403); return
                ruta_archivo = VIDEOS_DIR / video["archivo"]
                if ruta_archivo.exists(): ruta_archivo.unlink()
                if video.get("miniatura"):
                    ruta_thumb = THUMBS_DIR / video["miniatura"]
                    if ruta_thumb.exists(): ruta_thumb.unlink()
                datos["videos"] = [v for v in videos if v["id"] != video_id]
                datos.get("comentarios", {}).pop(video_id, None)
                guardar_datos(datos)
                self.enviar_json({"ok": True}); return
            m = re.match(r'^/api/videos/([a-zA-Z0-9]+)/comentarios/([a-zA-Z0-9]+)$', ruta)
            if m:
                video_id, comentario_id = m.group(1), m.group(2)
                usuario = self.usuario_autenticado()
                if not usuario:
                    self.enviar_error_json("Debes iniciar sesión.", 401); return
                datos = cargar_datos()
                lista = datos.get("comentarios", {}).get(video_id, [])
                comentario = next((c for c in lista if c["id"] == comentario_id), None)
                if not comentario:
                    self.enviar_error_json("Comentario no encontrado.", 404); return
                if comentario.get("apodo") != usuario:
                    self.enviar_error_json("No tienes permiso para borrar este comentario.", 403); return
                datos["comentarios"][video_id] = [c for c in lista if c["id"] != comentario_id]
                guardar_datos(datos)
                self.enviar_json({"ok": True}); return
            self.enviar_error_json("Ruta no encontrada.", 404)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            try: self.enviar_error_json(f"Error interno: {e}", 500)
            except: pass

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.end_headers()

if __name__ == "__main__":
    ip_local = obtener_ip_local()
    print("=" * 56)
    print("  🎬  MIGUELASGO TUBE - Servidor local (mejorado)")
    print("=" * 56)
    print(f"  En este equipo:   http://localhost:{PUERTO}")
    print(f"  En tu red local:  http://{ip_local}:{PUERTO}")
    print("=" * 56)
    if FFMPEG_DISPONIBLE:
        print("  ✅ ffmpeg detectado: se generarán miniaturas reales.")
    else:
        print("  ⚠️  ffmpeg no encontrado: no habrá miniaturas reales.")
    print("  🔐 Sistema de cuentas activo (usuario + contraseña).")
    print("=" * 56)
    print("  Comparte la segunda URL con otros dispositivos")
    print("  conectados a la MISMA red WiFi para que puedan")
    print("  ver y subir vídeos también.")
    print("=" * 56)
    print("  Pulsa CTRL+C para detener el servidor.")
    print()
    servidor = ThreadingHTTPServer(("0.0.0.0", PUERTO), TubeHandler)
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        print("\nServidor detenido.")
        servidor.shutdown()