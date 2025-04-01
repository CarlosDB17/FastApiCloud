import os
import json
import base64
from fastapi import FastAPI, HTTPException, Query, Form, File, UploadFile
from config import db  # Importamos la conexion a Firestore desde config.py
from pydantic import BaseModel, EmailStr, field_validator
from datetime import date, datetime
from typing import List, Optional
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi import Request
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
    usuario_ref = db.collection("usuarios").document(documento_identidad)

    if not usuario_ref.get().exists:
        raise HTTPException(status_code=404, detail="usuario no encontrado")

    # obtener los datos actuales del usuario
    usuario_actual = usuario_ref.get().to_dict()

    # verificar si se intenta actualizar el documento_identidad
    if usuario.documento_identidad and usuario.documento_identidad != documento_identidad:
        # verificar si el nuevo documento_identidad ya existe
        nuevo_usuario_ref = db.collection("usuarios").document(usuario.documento_identidad)
        if nuevo_usuario_ref.get().exists:
            raise HTTPException(status_code=400, detail="el nuevo documento de identidad ya esta registrado")

        # crear un nuevo documento con el nuevo documento_identidad
        nuevo_usuario_data = {**usuario_actual, **usuario.model_dump(exclude_unset=True)}
        nuevo_usuario_data["documento_identidad"] = usuario.documento_identidad

        # si se envia "nombre", actualizar tambien las versiones normalizadas
        if "nombre" in nuevo_usuario_data:
            nuevo_usuario_data["nombre_normalizado"] = normalizar_texto(nuevo_usuario_data["nombre"])
            nuevo_usuario_data["nombre_minusculas"] = nuevo_usuario_data["nombre"].lower()

        # si se envia "fecha_nacimiento", convertir a string si es un objeto date
        if "fecha_nacimiento" in nuevo_usuario_data:
            if isinstance(nuevo_usuario_data["fecha_nacimiento"], date):
                nuevo_usuario_data["fecha_nacimiento"] = nuevo_usuario_data["fecha_nacimiento"].strftime("%Y-%m-%d")

        nuevo_usuario_ref.set(nuevo_usuario_data)  # crear el nuevo documento
        usuario_ref.delete()  # eliminar el documento original

        return {"message": "usuario actualizado correctamente con nuevo documento de identidad"}

    # actualizar los datos del usuario actual
    update_data = usuario.model_dump(exclude_unset=True)

    # si se envia "nombre", actualizar tambien las versiones normalizadas
    if "nombre" in update_data:
        update_data["nombre_normalizado"] = normalizar_texto(update_data["nombre"])
        update_data["nombre_minusculas"] = update_data["nombre"].lower()

    # si se envia "fecha_nacimiento", convertir a string si es un objeto date
    if "fecha_nacimiento" in update_data:
        if isinstance(update_data["fecha_nacimiento"], date):
            update_data["fecha_nacimiento"] = update_data["fecha_nacimiento"].strftime("%Y-%m-%d")

# si se proporciona una foto, actualizarla
    if "foto" in update_data:
        update_data["foto"] = usuario.foto


    usuario_ref.update(update_data)  # actualizar el documento actual
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

    # Calcular el total de usuarios
    total = len(usuarios)

    # Aplicar paginación
    paginados = usuarios[skip : skip + limit]

    return {"usuarios": paginados, "total": total}

#endpoint para subir imagenes
@app.post("/usuarios/{documento_identidad}/foto")
async def subir_foto(documento_identidad: str, file: UploadFile = File(...)):
    usuario_ref = db.collection("usuarios").document(documento_identidad)

    # Verificar si el usuario existe
    if not usuario_ref.get().exists:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    # Validar que solo se haya enviado un archivo
    if not file:
        raise HTTPException(status_code=400, detail="No se ha enviado ningún archivo")
    if isinstance(file, list):
        raise HTTPException(status_code=400, detail="Solo se permite subir un archivo a la vez")

    # Llamar al endpoint para obtener la foto actual del usuario
    try:
        foto_actual = obtener_foto(documento_identidad)["foto"]  # Reutilizamos el endpoint
    except HTTPException as e:
        if e.status_code == 404:
            foto_actual = None  # El usuario no tiene foto
        else:
            raise e  # Relanzar otros errores

    # especificar el nombre del bucket explícitamente
    bucket = storage.bucket("pf25-carlos-db.firebasestorage.app")  # Reemplaza con tu bucket

    # eliminar la foto anterior si existe
    if foto_actual:
        blob_anterior = bucket.blob("/".join(foto_actual.split("/")[-2:]))  # Extraer carpeta y nombre del archivo
        if blob_anterior.exists():
            blob_anterior.delete()

    # validar el tipo de archivo (solo imágenes permitidas)
    if file.content_type not in ["image/jpeg", "image/png", "image/jpg", "image.heic", "image.heif"]:
        raise HTTPException(status_code=400, detail="Solo se permiten archivos PNG, JPG, JPEG, HEIC o HEIF")

    # subir la nueva foto
    blob = bucket.blob(f"usuarios/{documento_identidad}_{file.filename}")
    try:
        blob.upload_from_file(file.file, content_type=file.content_type)
        blob.make_public()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al subir la foto: {str(e)}")

    #hacer que el archivo sea público y obtener la URL
    public_url = blob.public_url

    # actualizar el campo foto del usuario con la nueva URL publica
    usuario_ref.update({"foto": public_url})

    return {"message": "Foto subida correctamente", "foto": public_url}


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

    # extraer el nombre del archivo del path completo
    bucket = storage.bucket("pf25-carlos-db.firebasestorage.app")  # Reemplaza con tu bucket
    blob_name = "/".join(foto_actual.split("/")[-2:])  # Extraer la carpeta y el nombre del archivo
    blob = bucket.blob(blob_name)

    # verificar si el archivo existe antes de intentar eliminarlo
    if not blob.exists():
        raise HTTPException(status_code=404, detail="El archivo no existe en el bucket")

    # Eliminar el archivo del bucket
    blob.delete()

    # Actualizar el campo foto en Firestore
    usuario_ref.update({"foto": None})

    return {"message": "Foto borrada correctamente"}

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