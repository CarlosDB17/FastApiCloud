import os
import json
import base64
import csv
from io import StringIO
from fastapi import UploadFile, File, Form, HTTPException
from fastapi import FastAPI, HTTPException, Query, Form, File, UploadFile
from config import db  # Importamos la conexion a Firestore desde config.py
from pydantic import BaseModel, EmailStr, field_validator
from datetime import date, datetime
from typing import List, Optional
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi import Request, Body
from fastapi.staticfiles import StaticFiles
from firebase_admin import storage  # Solo importa storage si lo necesitas

app = FastAPI()

# habilitar cors para permitir peticiones desde el frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # permitir cualquier origen
    allow_credentials=True,
    allow_methods=["*"],  # permitir todos los métodos (GET, POST, etc.)
    allow_headers=["*"],  # permitir todos los encabezados
)

# montar el directorio 'uploads' para servir archivos estaticos
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")

import unicodedata
import re  # para validar el dni

# funcion para normalizar texto (eliminar acentos y convertir a minusculas)
def normalizar_texto(texto: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", texto.lower()) if unicodedata.category(c) != "Mn"
    )


# modelo de usuario con validaciones
class Usuario(BaseModel):
    nombre: str
    email: EmailStr
    documento_identidad: str
    fecha_nacimiento: date
    foto: Optional[str] = None  # nuevo campo para almacenar la URL o nombre del archivo de la foto

    @field_validator("documento_identidad")
    def validar_documento_identidad(cls, documento_identidad):
        if not re.match(r"^[a-zA-Z0-9]{6,15}$", documento_identidad):
            raise ValueError("el documento de identidad debe ser alfanumerico y tener entre 6 y 15 caracteres")
        return documento_identidad

    @field_validator("fecha_nacimiento", mode="before")  # interceptar antes de la validacion
    @classmethod
    def validar_fecha_nacimiento(cls, fecha):
        if isinstance(fecha, str):  # convertir la cadena a date si es necesario
            try:
                fecha = datetime.strptime(fecha, "%Y-%m-%d").date()
            except ValueError:
                raise ValueError("formato de fecha incorrecto. debe ser YYYY-MM-DD")

        if fecha >= date.today():
            raise ValueError("la fecha de nacimiento no puede ser hoy ni en el futuro")
        return fecha
    

# modelo de usuario con datos opcionales para actualizacion parcial
class UsuarioUpdate(BaseModel):
    nombre: Optional[str] = None
    email: Optional[EmailStr] = None
    fecha_nacimiento: Optional[date] = None
    documento_identidad: Optional[str] = None
    foto: Optional[str] = None  # Nuevo campo opcional para actualizar la foto

    @field_validator("fecha_nacimiento", mode="before")
    @classmethod
    def validar_fecha_nacimiento(cls, fecha):
        if fecha:
            if isinstance(fecha, str):
                try:
                    fecha = datetime.strptime(fecha, "%Y-%m-%d").date()
                except ValueError:
                    raise ValueError("formato de fecha incorrecto. debe ser YYYY-MM-DD")

            if fecha >= date.today():
                raise ValueError("la fecha de nacimiento no puede ser hoy ni en el futuro")
        return fecha

# el endpoint de registro
@app.post("/usuarios", response_model=dict)
async def registrar_usuario(usuario: Usuario):
    try:
        # Convertir el documento de identidad a mayúsculas
        usuario.documento_identidad = usuario.documento_identidad.upper()
        
        usuario_ref = db.collection("usuarios").document(usuario.documento_identidad)

        # Verificar si el usuario ya existe
        if usuario_ref.get().exists:
            raise HTTPException(status_code=400, detail="Este documento de identidad ya ha sido registrado")

        # Verificar si el email ya existe
        email_ref = db.collection("usuarios").where("email", "==", usuario.email.lower()).get()
        if email_ref:
            raise HTTPException(status_code=400, detail="Este email ya ha sido registrado")

        # Preparar los datos del usuario
        usuario_dict = usuario.model_dump()
        usuario_dict["fecha_nacimiento"] = usuario.fecha_nacimiento.strftime("%Y-%m-%d")
        usuario_dict["nombre_normalizado"] = normalizar_texto(usuario.nombre)
        usuario_dict["nombre_minusculas"] = usuario.nombre.lower()
        usuario_dict["email"] = usuario.email.lower()
        usuario_dict["documento_identidad"] = usuario.documento_identidad.upper()

        # Guardar en Firestore
        usuario_ref.set(usuario_dict)

        return {"message": "Usuario registrado correctamente", "usuario": usuario_dict}

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error interno del servidor: {str(e)}")

# endpoint para obtener un usuario por su documento de identidad
@app.get("/usuarios/{documento_identidad}", response_model=Usuario)
def obtener_usuario(documento_identidad: str):
    # convertir el documento de identidad recibido a mayusculas
    documento_identidad = documento_identidad.upper()

    usuario_doc = db.collection("usuarios").document(documento_identidad).get()

    if not usuario_doc.exists:
        raise HTTPException(status_code=404, detail="usuario no encontrado")

    usuario_data = usuario_doc.to_dict()
    usuario_data["fecha_nacimiento"] = date.fromisoformat(usuario_data["fecha_nacimiento"])
    return Usuario(**usuario_data)

# endpoint para actualizar un usuario por su documento de identidad
@app.patch("/usuarios/{documento_identidad}", response_model=dict)
def actualizar_usuario_parcial(documento_identidad: str, usuario: UsuarioUpdate):
    print(f"Documento actual recibido en la URL: {documento_identidad}")
    print(f"Datos recibidos en el cuerpo: {usuario}")

    usuario_ref = db.collection("usuarios").document(documento_identidad)

    if not usuario_ref.get().exists:
        print("Error: Usuario no encontrado")
        raise HTTPException(status_code=404, detail="usuario no encontrado")

    usuario_actual = usuario_ref.get().to_dict()
    print(f"Datos actuales del usuario: {usuario_actual}")

    if usuario.documento_identidad and usuario.documento_identidad != documento_identidad:
        print(f"Intentando cambiar el documento_identidad de {documento_identidad} a {usuario.documento_identidad}")
        nuevo_usuario_ref = db.collection("usuarios").document(usuario.documento_identidad)
        if nuevo_usuario_ref.get().exists:
            print("Error: El nuevo documento de identidad ya está registrado")
            raise HTTPException(status_code=400, detail="el nuevo documento de identidad ya esta registrado")

        nuevo_usuario_data = {**usuario_actual, **usuario.model_dump(exclude_unset=True)}
        nuevo_usuario_data["documento_identidad"] = usuario.documento_identidad

        # Convertir fecha_nacimiento a string si está presente
        if "fecha_nacimiento" in nuevo_usuario_data and isinstance(nuevo_usuario_data["fecha_nacimiento"], date):
            nuevo_usuario_data["fecha_nacimiento"] = nuevo_usuario_data["fecha_nacimiento"].strftime("%Y-%m-%d")

        print(f"Datos del nuevo usuario: {nuevo_usuario_data}")

        nuevo_usuario_ref.set(nuevo_usuario_data)
        print("Nuevo usuario creado")

        usuario_ref.delete()
        print("Usuario antiguo eliminado")

        return {
            "message": "usuario actualizado correctamente con nuevo documento de identidad",
            "nuevo_documento_identidad": usuario.documento_identidad,
        }

    update_data = usuario.model_dump(exclude_unset=True)
    print(f"Datos a actualizar: {update_data}")

    # Convertir fecha_nacimiento a string si está presente
    if "fecha_nacimiento" in update_data and isinstance(update_data["fecha_nacimiento"], date):
        update_data["fecha_nacimiento"] = update_data["fecha_nacimiento"].strftime("%Y-%m-%d")

    if not update_data:
        print("Error: No se proporcionaron datos para actualizar")
        raise HTTPException(status_code=400, detail="No se proporcionaron datos para actualizar.")

    usuario_ref.update(update_data)
    print("Usuario actualizado correctamente")

    return {"message": "usuario actualizado correctamente", "actualizado": update_data}

# endpoint para eliminar un usuario por su documento de identidad
@app.delete("/usuarios/{documento_identidad}", response_model=dict)
def eliminar_usuario(documento_identidad: str):
    usuario_ref = db.collection("usuarios").document(documento_identidad)

    # Verificar si el usuario existe
    if not usuario_ref.get().exists:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    # Llamar al endpoint para borrar la foto del usuario
    try:
        borrar_foto(documento_identidad)
    except HTTPException as e:
        if e.status_code != 404:  # Ignorar si no tiene foto, pero relanzar otros errores
            raise e

    # Eliminar el documento del usuario en Firestore
    usuario_ref.delete()

    return {"message": "Usuario y su foto eliminados correctamente"}

# endpoint para buscar usuarios por email (búsqueda parcial)
@app.get("/usuarios/email/{email}", response_model=dict)
def buscar_por_email(email: str, skip: int = 0, limit: int = 3):
    email = email.lower()
    usuarios_ref = db.collection("usuarios").stream()
    usuarios = []

    for user in usuarios_ref:
        user_data = user.to_dict()
        user_data["fecha_nacimiento"] = date.fromisoformat(user_data["fecha_nacimiento"])
        if email in user_data.get("email", ""):
            usuarios.append(Usuario(**user_data))

    if not usuarios:
        raise HTTPException(status_code=404, detail="No se encontraron usuarios con ese email")

    # Calcular el total de usuarios encontrados
    total = len(usuarios)

    # Aplicar paginación
    paginados = usuarios[skip : skip + limit]

    return {"usuarios": paginados, "total": total}

# endpoint para buscar usuarios por nombre sin importar mayusculas ni acentos
@app.get("/usuarios/nombre/{nombre}", response_model=dict)
def buscar_por_nombre(nombre: str, skip: int = 0, limit: int = 3):
    nombre_normalizado = normalizar_texto(nombre)

    try:
        # buscar usuarios cuyo nombre normalizado contenga la palabra clave
        usuarios_ref = db.collection("usuarios").stream()

        usuarios = []
        for user in usuarios_ref:
            user_data = user.to_dict()
            user_data["fecha_nacimiento"] = date.fromisoformat(user_data["fecha_nacimiento"])
            
            # comparar el nombre normalizado con la palabra clave
            if nombre_normalizado in user_data.get("nombre_normalizado", ""):
                usuarios.append(Usuario(**user_data))

        if not usuarios:
            raise HTTPException(status_code=404, detail="no se encontraron usuarios con ese nombre")

        # Calcular el total de usuarios encontrados
        total = len(usuarios)

        # Aplicar paginación
        paginados = usuarios[skip : skip + limit]

        return {"usuarios": paginados, "total": total}

    except HTTPException as http_exc:
        # relanzar excepciones http ya controladas
        raise http_exc

    except Exception as e:
        # manejar errores inesperados
        raise HTTPException(status_code=500, detail="error interno del servidor")

# endpoint para buscar usuarios por documento de identidad (busqueda parcial)
@app.get("/usuarios/documento/{documento_identidad}", response_model=dict)
def buscar_por_documento(documento_identidad: str, skip: int = 0, limit: int = 3):
    # convertir el documento de identidad recibido a mayusculas
    documento_identidad = documento_identidad.upper()

    try:
        # buscar usuarios cuyo documento de identidad contenga el valor buscado
        usuarios_ref = db.collection("usuarios").stream()
        usuarios = []

        for user in usuarios_ref:
            user_data = user.to_dict()
            user_data["fecha_nacimiento"] = date.fromisoformat(user_data["fecha_nacimiento"])

            # comparar si el documento de identidad contiene el valor buscado
            if documento_identidad in user_data.get("documento_identidad", ""):
                usuarios.append(Usuario(**user_data))

        # si no se encontraron usuarios lanzar un error 404
        if not usuarios:
            raise HTTPException(status_code=404, detail="No se encontraron usuarios con ese documento de identidad")

        # Calcular el total de usuarios encontrados
        total = len(usuarios)

        # Aplicar paginación
        paginados = usuarios[skip : skip + limit]

        return {"usuarios": paginados, "total": total}

    except HTTPException as http_exc:
        # relanzar excepciones HTTP ya controladas
        raise http_exc

    except Exception as e:
        # manejar errores inesperados
        raise HTTPException(status_code=500, detail="Error interno del servidor")
    
#end point para buscar usuarios por varios criterios (documento, nombre y email)
@app.get("/usuarios/buscar/{valor}", response_model=dict)
def buscar_usuarios_por_ruta(
    valor: str,
    skip: int = 0,
    limit: int = 3
):
    try:
        usuarios_ref = db.collection("usuarios").stream()
        usuarios = []

        for user in usuarios_ref:
            user_data = user.to_dict()
            user_data["fecha_nacimiento"] = date.fromisoformat(user_data["fecha_nacimiento"])

            # Filtrar por documento de identidad
            if valor.upper() in user_data.get("documento_identidad", ""):
                usuarios.append(Usuario(**user_data))
                continue

            # Filtrar por nombre normalizado
            if normalizar_texto(valor) in user_data.get("nombre_normalizado", ""):
                usuarios.append(Usuario(**user_data))
                continue

            # Filtrar por email
            if valor.lower() in user_data.get("email", ""):
                usuarios.append(Usuario(**user_data))
                continue

        # Si no se encontraron usuarios, lanzar un error 404
        if not usuarios:
            raise HTTPException(status_code=404, detail="No se encontraron usuarios con el valor proporcionado")

        # Calcular el total de usuarios encontrados
        total = len(usuarios)

        # Aplicar paginación
        paginados = usuarios[skip : skip + limit]

        return {"usuarios": paginados, "total": total}

    except HTTPException as http_exc:
        # Relanzar excepciones HTTP ya controladas
        raise http_exc

    except Exception as e:
        # Manejar errores inesperados
        raise HTTPException(status_code=500, detail=f"Error interno del servidor: {str(e)}")
    

# endpoint para obtener todos los usuarios con paginación y total
@app.get("/usuarios", response_model=dict)
def obtener_todos_los_usuarios(skip: int = 0, limit: int = 3):
    usuarios_ref = db.collection("usuarios").stream()
    usuarios = []

    for user in usuarios_ref:
        user_data = user.to_dict()
        user_data["fecha_nacimiento"] = date.fromisoformat(user_data["fecha_nacimiento"])
        usuarios.append(Usuario(**user_data))

    if not usuarios:
        raise HTTPException(status_code=404, detail="No hay usuarios registrados")

    # calcular el total de usuarios
    total = len(usuarios)

    # aplicar paginación
    paginados = usuarios[skip : skip + limit]

    return {"usuarios": paginados, "total": total}

#endpoint para subir imagenes
@app.post("/usuarios/{documento_identidad}/foto")
async def subir_foto(documento_identidad: str, file: UploadFile = File(...)):
    try:
        usuario_ref = db.collection("usuarios").document(documento_identidad)

        # Verificar si el usuario existe
        if not usuario_ref.get().exists:
            raise HTTPException(status_code=404, detail="Usuario no encontrado")

        # Validar que solo se haya enviado un archivo
        if not file:
            raise HTTPException(status_code=400, detail="No se ha enviado ningún archivo")

        # Verificar el content-type del archivo
        content_type = file.content_type
        print(f"Content-Type recibido: {content_type}")  # Log para depuración
        
        # Lista completa de tipos MIME de imágenes permitidos (corregido)
        tipos_permitidos = [
            "image/jpeg", "image/png", "image/jpg", 
            "image/heic", "image/heif", "image/webp",
            # Android puede enviar variantes
            "application/octet-stream"  # Android a veces usa esto para archivos
        ]
        
        if content_type not in tipos_permitidos:
            raise HTTPException(
                status_code=400, 
                detail=f"Tipo de archivo no permitido: {content_type}. Solo se permiten PNG, JPG, JPEG, HEIC, HEIF o WEBP"
            )

        # Intentar leer los primeros bytes para verificar si es una imagen real
        try:
            content = await file.read(1024)  # Leer solo los primeros bytes
            file.file.seek(0)  # Regresar al inicio del archivo
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"No se pudo leer el archivo: {str(e)}")

        # Llamar al endpoint para obtener la foto actual del usuario
        try:
            foto_actual = obtener_foto(documento_identidad)["foto"]
        except HTTPException as e:
            if e.status_code == 404:
                foto_actual = None  # El usuario no tiene foto
            else:
                raise e  # Relanzar otros errores

        # Especificar el nombre del bucket explícitamente
        bucket = storage.bucket("pf25-carlos-db.firebasestorage.app")

        # Eliminar la foto anterior si existe
        if foto_actual:
            try:
                blob_anterior = bucket.blob("/".join(foto_actual.split("/")[-2:]))
                if blob_anterior.exists():
                    blob_anterior.delete()
            except Exception as e:
                print(f"Error al eliminar foto anterior: {str(e)}")  # Log, pero no interrumpir

        # Generar un nombre seguro para el archivo (evitar caracteres especiales)
        import time
        safe_filename = f"{documento_identidad}_{int(time.time())}.jpg"
        blob = bucket.blob(f"usuarios/{safe_filename}")
        
        # Subir la nueva foto
        try:
            blob.upload_from_file(file.file, content_type=file.content_type)
            blob.make_public()
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Error al subir la foto: {str(e)}")

        # Hacer que el archivo sea público y obtener la URL
        public_url = blob.public_url

        # Actualizar el campo foto del usuario con la nueva URL pública
        usuario_ref.update({"foto": public_url})

        return {"message": "Foto subida correctamente", "foto": public_url}
        
    except HTTPException as e:
        # Relanzar excepciones HTTP
        raise e
    except Exception as e:
        # Log detallado para depuración
        import traceback
        print(f"Error al subir foto: {str(e)}")
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Error interno al subir la foto: {str(e)}")


# endpoint para borrar la foto de un usuario
@app.delete("/usuarios/{documento_identidad}/foto", response_model=dict)
def borrar_foto(documento_identidad: str):
    usuario_ref = db.collection("usuarios").document(documento_identidad)

    # verificar si el usuario existe
    if not usuario_ref.get().exists:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    # obtener la URL de la foto actual
    usuario_data = usuario_ref.get().to_dict()
    foto_actual = usuario_data.get("foto")

    if not foto_actual:
        raise HTTPException(status_code=404, detail="Este usuario no tiene foto para borrar")

    # Verificar si la URL parece ser de Firebase Storage
    if "firebasestorage" in foto_actual:
        try:
            # extraer el nombre del archivo del path completo
            bucket = storage.bucket("pf25-carlos-db.firebasestorage.app")
            blob_name = "/".join(foto_actual.split("/")[-2:])  # Extraer la carpeta y el nombre del archivo
            blob = bucket.blob(blob_name)

            # verificar si el archivo existe antes de intentar eliminarlo
            if blob.exists():
                blob.delete()
                print(f"Archivo {blob_name} eliminado correctamente del bucket")
            else:
                print(f"Advertencia: El archivo {blob_name} no existe en el bucket")
        except Exception as e:
            print(f"Error al intentar eliminar archivo del bucket: {str(e)}")
            # Continuamos con la operación a pesar del error
    else:
        print(f"La URL {foto_actual} no parece ser de Firebase Storage")

    # Actualizar el campo foto en Firestore siempre, independientemente de si se pudo borrar el archivo
    usuario_ref.update({"foto": None})

    return {"message": "Campo de foto limpiado correctamente"}

#endpoint para obtener la foto de un usuario
@app.get("/usuarios/{documento_identidad}/foto")
def obtener_foto(documento_identidad: str):
    usuario_ref = db.collection("usuarios").document(documento_identidad)

    if not usuario_ref.get().exists:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    usuario_data = usuario_ref.get().to_dict()
    foto = usuario_data.get("foto")

    if not foto:
        raise HTTPException(status_code=404, detail="Este usuario no tiene foto")

    return {"foto": foto}


# endpoint para buscar un usuario por email exacto
@app.get("/usuarios/email-exacto/{email}", response_model=Usuario)
def buscar_por_email_exacto(email: str):
    email = email.lower()
    usuarios_ref = db.collection("usuarios").where("email", "==", email).get()

    if not usuarios_ref:
        raise HTTPException(status_code=404, detail="No se encontró un usuario con ese email")

    usuario_data = usuarios_ref[0].to_dict()
    usuario_data["fecha_nacimiento"] = date.fromisoformat(usuario_data["fecha_nacimiento"])
    return Usuario(**usuario_data)

# endpoint para buscar un usuario por documento exacto
@app.get("/usuarios/documento-exacto/{documento_identidad}", response_model=Usuario)
def buscar_por_documento_exacto(documento_identidad: str):
    documento_identidad = documento_identidad.upper()
    usuario_ref = db.collection("usuarios").document(documento_identidad).get()

    if not usuario_ref.exists:
        raise HTTPException(status_code=404, detail="No se encontró un usuario con ese documento de identidad")

    usuario_data = usuario_ref.to_dict()
    usuario_data["fecha_nacimiento"] = date.fromisoformat(usuario_data["fecha_nacimiento"])
    return Usuario(**usuario_data)

#endpoint para registrar varios usuarios a la vez
@app.post("/usuarios/multiples", response_model=dict)
async def registrar_usuarios_multiples(usuarios: List[Usuario]):
    resultados = []
    usuarios_procesados = 0
    usuarios_registrados = 0
    usuarios_con_error = 0
    
    for usuario in usuarios:
        try:
            # Validación del documento de identidad
            if not re.match(r"^[a-zA-Z0-9]{6,15}$", usuario.documento_identidad):
                resultados.append({
                    "usuario": usuario.documento_identidad, 
                    "status": "Error", 
                    "mensaje": "El documento de identidad debe ser alfanumérico y tener entre 6 y 15 caracteres."
                })
                usuarios_con_error += 1
                continue
            
            # Convertir a mayúsculas el documento de identidad
            usuario.documento_identidad = usuario.documento_identidad.upper()
            usuario_ref = db.collection("usuarios").document(usuario.documento_identidad)

            # Verificar si el usuario ya existe
            if usuario_ref.get().exists:
                resultados.append({
                    "usuario": usuario.documento_identidad, 
                    "status": "Error", 
                    "mensaje": "El documento ya está registrado."
                })
                usuarios_con_error += 1
                continue

            # Validar formato de email y convertir a minúsculas
            usuario.email = usuario.email.lower()
            
            # Verificar si el email ya existe
            email_ref = db.collection("usuarios").where("email", "==", usuario.email).get()
            if email_ref:
                resultados.append({
                    "usuario": usuario.documento_identidad, 
                    "status": "Error", 
                    "mensaje": f"El email {usuario.email} ya está registrado con otro usuario."
                })
                usuarios_con_error += 1
                continue
            
            # Validar fecha de nacimiento
            if usuario.fecha_nacimiento >= date.today():
                resultados.append({
                    "usuario": usuario.documento_identidad, 
                    "status": "Error", 
                    "mensaje": "La fecha de nacimiento no puede ser hoy ni en el futuro."
                })
                usuarios_con_error += 1
                continue
            
            # Validar que el nombre no esté vacío
            if not usuario.nombre or usuario.nombre.strip() == "":
                resultados.append({
                    "usuario": usuario.documento_identidad, 
                    "status": "Error", 
                    "mensaje": "El nombre no puede estar vacío."
                })
                usuarios_con_error += 1
                continue

            # Preparar los datos del usuario
            usuario_dict = usuario.model_dump()
            usuario_dict["fecha_nacimiento"] = usuario.fecha_nacimiento.strftime("%Y-%m-%d")
            usuario_dict["nombre_normalizado"] = normalizar_texto(usuario.nombre)
            usuario_dict["nombre_minusculas"] = usuario.nombre.lower()
            usuario_dict["email"] = usuario.email
            usuario_dict["documento_identidad"] = usuario.documento_identidad
            
            # Si se proporcionó una URL de foto, la mantenemos
            if usuario.foto:
                usuario_dict["foto"] = usuario.foto

            # Guardar en Firestore
            usuario_ref.set(usuario_dict)
            resultados.append({
                "usuario": usuario.documento_identidad, 
                "status": "Éxito", 
                "mensaje": "Usuario registrado correctamente."
            })
            usuarios_registrados += 1
            
        except ValueError as ve:
            # Capturar errores de validación específicos
            resultados.append({
                "usuario": getattr(usuario, "documento_identidad", "desconocido"), 
                "status": "Error", 
                "mensaje": f"Error de validación: {str(ve)}"
            })
            usuarios_con_error += 1
        except Exception as e:
            # Capturar otros errores inesperados
            resultados.append({
                "usuario": getattr(usuario, "documento_identidad", "desconocido"), 
                "status": "Error", 
                "mensaje": f"Error inesperado: {str(e)}"
            })
            usuarios_con_error += 1
        
        usuarios_procesados += 1

    # Resumen de la operación
    return {
        "resultados": resultados,
        "resumen": {
            "total_procesados": usuarios_procesados,
            "registrados_correctamente": usuarios_registrados,
            "con_errores": usuarios_con_error
        }
    }

#endpoint para registrar usuarios desde un archivo CSV
@app.post("/usuarios/csv", response_model=dict)
async def registrar_usuarios_csv(file: UploadFile):
    if file.content_type != "text/csv":
        raise HTTPException(status_code=400, detail="El archivo debe ser un CSV")

    resultados = []
    usuarios_procesados = 0
    usuarios_registrados = 0
    usuarios_con_error = 0
    
    try:
        contenido = await file.read()
        csv_data = StringIO(contenido.decode("utf-8"))
        reader = csv.DictReader(csv_data)

        for row in reader:
            usuarios_procesados += 1
            try:
                # Verificar que todos los campos requeridos estén presentes
                campos_requeridos = ["nombre", "email", "documento_identidad", "fecha_nacimiento"]
                campos_faltantes = [campo for campo in campos_requeridos if campo not in row or not row[campo]]
                
                if campos_faltantes:
                    resultados.append({
                        "usuario": row.get("documento_identidad", "desconocido"), 
                        "status": "error", 
                        "mensaje": f"Faltan campos requeridos: {', '.join(campos_faltantes)}"
                    })
                    usuarios_con_error += 1
                    continue
                
                # Validar documento de identidad
                if not re.match(r"^[a-zA-Z0-9]{6,15}$", row["documento_identidad"]):
                    resultados.append({
                        "usuario": row["documento_identidad"], 
                        "status": "error", 
                        "mensaje": "El documento de identidad debe ser alfanumérico y tener entre 6 y 15 caracteres"
                    })
                    usuarios_con_error += 1
                    continue
                
                # Crear usuario con o sin foto, dependiendo si existe en el CSV
                usuario_params = {
                    "nombre": row["nombre"],
                    "email": row["email"],
                    "documento_identidad": row["documento_identidad"],
                    "fecha_nacimiento": row["fecha_nacimiento"]
                }
                
                # Añadir foto si está presente en el CSV
                if "foto" in row and row["foto"]:
                    usuario_params["foto"] = row["foto"]
                
                # Intentar crear el objeto Usuario (esto validará el email y la fecha)
                try:
                    usuario = Usuario(**usuario_params)
                except ValueError as ve:
                    resultados.append({
                        "usuario": row["documento_identidad"], 
                        "status": "error", 
                        "mensaje": f"Error de validación: {str(ve)}"
                    })
                    usuarios_con_error += 1
                    continue
                
                # Convertir a mayúsculas el documento de identidad
                usuario.documento_identidad = usuario.documento_identidad.upper()
                usuario_ref = db.collection("usuarios").document(usuario.documento_identidad)

                # Verificar si el usuario ya existe
                if usuario_ref.get().exists:
                    resultados.append({
                        "usuario": usuario.documento_identidad, 
                        "status": "error", 
                        "mensaje": "El documento de identidad ya está registrado."
                    })
                    usuarios_con_error += 1
                    continue

                # Validar formato de email y convertir a minúsculas
                usuario.email = usuario.email.lower()
                
                # Verificar si el email ya existe
                email_ref = db.collection("usuarios").where("email", "==", usuario.email).get()
                if email_ref:
                    resultados.append({
                        "usuario": usuario.documento_identidad, 
                        "status": "error", 
                        "mensaje": f"El email {usuario.email} ya está registrado con otro usuario."
                    })
                    usuarios_con_error += 1
                    continue

                # Preparar los datos del usuario
                usuario_dict = usuario.model_dump()
                usuario_dict["fecha_nacimiento"] = usuario.fecha_nacimiento.strftime("%Y-%m-%d")
                usuario_dict["nombre_normalizado"] = normalizar_texto(usuario.nombre)
                usuario_dict["nombre_minusculas"] = usuario.nombre.lower()
                usuario_dict["email"] = usuario.email
                usuario_dict["documento_identidad"] = usuario.documento_identidad

                # Guardar en Firestore
                usuario_ref.set(usuario_dict)
                resultados.append({
                    "usuario": usuario.documento_identidad, 
                    "status": "éxito", 
                    "mensaje": "Usuario registrado correctamente"
                })
                usuarios_registrados += 1
                
            except Exception as e:
                resultados.append({
                    "usuario": row.get("documento_identidad", "desconocido"), 
                    "status": "error", 
                    "mensaje": f"Error inesperado: {str(e)}"
                })
                usuarios_con_error += 1

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al procesar el archivo CSV: {str(e)}")

    # Resumen de la operación
    return {
        "resultados": resultados,
        "resumen": {
            "total_procesados": usuarios_procesados,
            "registrados_correctamente": usuarios_registrados,
            "con_errores": usuarios_con_error
        }
    }

# mensaje de bienvenida en la raiz
@app.get("/")
def raiz():
    return {"message": "fastapi esta funcionando correctamente"}

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    errores = exc.errors()
    mensajes = []

    for error in errores:
        campo = ".".join(error["loc"][1:])  # obtener el campo que fallo
        mensaje = error["msg"]  # mensaje de error

        # personalizar el mensaje para el campo "email"
        if campo == "email":
            mensajes.append("Error en el campo email: valor no válido. Asegurate de escribir el email bien.")
        # personalizar el mensaje para el campo "fecha_nacimiento"
        elif campo == "fecha_nacimiento":
            mensajes.append("Error en el campo fecha de nacimiento: la fecha no puede ser hoy ni en el futuro.")
        else:
            mensajes.append(f"Error en '{campo}': {mensaje}")

    return JSONResponse(
        status_code=422,
        content={"detail": mensajes},
    )