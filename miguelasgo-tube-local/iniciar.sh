#!/bin/bash
# ============================================================
#  Miguelasgo Tube - Lanzador para Linux
#  Doble clic (o "Ejecutar" / "Run in terminal") para arrancar.
#
#  No necesita pip ni instalar nada: usa solo la librería
#  estándar de Python 3.
# ============================================================

# Nos colocamos en la carpeta donde está este script,
# sin importar desde dónde lo hayan ejecutado.
cd "$(dirname "$0")"

echo "============================================================"
echo "  🎬  MIGUELASGO TUBE - Iniciando..."
echo "============================================================"

# ── Comprobar que Python 3 existe ──────────────────────────────
if ! command -v python3 &> /dev/null; then
    echo "❌ No se encontró 'python3' en este equipo."
    echo "   Instálalo desde tu gestor de paquetes e inténtalo de nuevo."
    read -p "Pulsa ENTER para cerrar..."
    exit 1
fi

# ── Abrir el navegador automáticamente al cabo de 2 segundos ───
(
    sleep 2
    if command -v xdg-open &> /dev/null; then
        xdg-open "http://localhost:8000" &> /dev/null
    elif command -v gio &> /dev/null; then
        gio open "http://localhost:8000" &> /dev/null
    fi
) &

# ── Arrancar el servidor (en primer plano, para ver los logs) ──
python3 server.py

# Si el servidor termina o falla, no cerrar la ventana de golpe
echo ""
echo "El servidor se ha detenido."
read -p "Pulsa ENTER para cerrar esta ventana..."
