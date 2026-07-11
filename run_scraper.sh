#!/bin/bash
# Script de ejecución fácil para el Scraper de Wallapop

# Directorio del script
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR"

# Verificar si existe el entorno virtual
if [ ! -d "venv" ]; then
    echo "Creando entorno virtual de Python..."
    python3 -m venv venv
    if [ $? -ne 0 ]; then
        echo "Error: No se pudo crear el entorno virtual. Asegúrate de tener python3 instalado."
        exit 1
    fi
fi

# Activar el entorno virtual
source venv/bin/activate

# Instalar dependencias si es necesario o si requirements.txt cambió
# (un simple check rápido)
if [ -f "requirements.txt" ]; then
    echo "Verificando/instalando dependencias..."
    pip install -q -r requirements.txt
fi

# Ejecutar el scraper con todos los argumentos provistos al script
echo "Ejecutando el scraper con los argumentos: $@"
python3 scraper.py "$@"
