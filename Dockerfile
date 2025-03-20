# usamos una imagen base ligera de Python
FROM python:3.11-slim

# definimos el directorio de trabajo dentro del contenedor
WORKDIR /app

# copiamos los archivos necesarios
COPY requirements.txt .

# instalamos las dependencias
RUN pip install --no-cache-dir -r requirements.txt

# copiamos el codigo de la aplicacion
COPY . .

# expongo/abro el puerto que usara uvicorn
EXPOSE 8080

# comando para ejecutar la aplicación en google cloud run
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]