from fastapi import FastAPI, HTTPException, Query
from config import db  # importamos la conexion a firestore
from pydantic import BaseModel, EmailStr, field_validator
from datetime import date, datetime
from typing import List, Optional
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi import Request

import unicodedata
import re  # para validar el dni


app = FastAPI()


# habilitar cors para permitir peticiones desde el frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # permitir cualquier origen
    allow_credentials=True,
    allow_methods=["*"],  # permitir todos los metodos (get, post..)
    allow_headers=["*"],  # permitir todos los encabezados
)

# funcion para normalizar texto (eliminar acentos y convertir a minusculas)
def normalizar_texto(texto: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", texto.lower()) if unicodedata.category(c) != "Mn"
    )


# modelo de usuario con validaciones
class Usuario(BaseModel):
    nombre: str
    email: EmailStr
    documento_identidad: str  # campo generico para dni, pasaporte, nie, etc.
    fecha_nacimiento: date

    @field_validator("documento_identidad")
    def validar_documento_identidad(cls, documento_identidad):
        if not re.match(r"^[a-zA-Z0-9]{6,15}$", documento_identidad):
            raise ValueError("el documento de identidad debe ser alfanumerico y tener entre 6 y 15 caracteres")
        return documento_identidad

    @field_validator("fecha_nacimiento", mode="before")  # interceptar antes de la validacion
    @classmethod
    def validar_fecha_nacimiento(cls, fecha):
        if isinstance(fecha, str):  # convertir la cadena a `date` si es necesario
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

# endpoint para registrar un nuevo usuario
@app.post("/usuarios", response_model=dict)
def registrar_usuario(usuario: Usuario):
    usuario_ref = db.collection("usuarios").document(usuario.documento_identidad)

    # verificar si el usuario ya existe por documento de identidad
    if usuario_ref.get().exists:
        raise HTTPException(status_code=400, detail="este documento de identidad ya ha sido registrado")

    # verificar si el email ya existe
    email_ref = db.collection("usuarios").where("email", "==", usuario.email).get()
    if email_ref:
        raise HTTPException(status_code=400, detail="este email ya ha sido registrado")

    # convertir la fecha a string porque firestore no admite el tipo date
    usuario_dict = usuario.model_dump()
    usuario_dict["fecha_nacimiento"] = usuario.fecha_nacimiento.strftime("%Y-%m-%d")

    # guardar el nombre normalizado (sin acentos y en minusculas) para mejorar busquedas
    usuario_dict["nombre_normalizado"] = normalizar_texto(usuario.nombre)

    # guardar tambien el nombre en minusculas para consultas exactas sin mayusculas
    usuario_dict["nombre_minusculas"] = usuario.nombre.lower()

    # guardar en firestore
    usuario_ref.set(usuario_dict)

    return {"message": "usuario registrado correctamente", "usuario": usuario_dict}

# endpoint para obtener un usuario por su documento de identidad
@app.get("/usuarios/{documento_identidad}", response_model=Usuario)
def obtener_usuario(documento_identidad: str):
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

    usuario_ref.update(update_data)  # actualizar el documento actual
    return {"message": "usuario actualizado correctamente", "actualizado": update_data}

# endpoint para eliminar un usuario por su documento de identidad
@app.delete("/usuarios/{documento_identidad}", response_model=dict)
def eliminar_usuario(documento_identidad: str):
    usuario_ref = db.collection("usuarios").document(documento_identidad)

    if not usuario_ref.get().exists:
        raise HTTPException(status_code=404, detail="usuario no encontrado")

    usuario_ref.delete()
    return {"message": "usuario eliminado correctamente"}

# endpoint para buscar usuarios por email
@app.get("/usuarios/email/{email}", response_model=List[Usuario])
def buscar_por_email(email: str):
    usuarios_ref = db.collection("usuarios").where("email", "==", email).stream()
    usuarios = []

    for user in usuarios_ref:
        user_data = user.to_dict()
        user_data["fecha_nacimiento"] = date.fromisoformat(user_data["fecha_nacimiento"])
        usuarios.append(Usuario(**user_data))

    if not usuarios:
        raise HTTPException(status_code=404, detail="no se encontraron usuarios con ese email")

    return usuarios

# endpoint para buscar usuarios por nombre sin importar mayusculas ni acentos
@app.get("/usuarios/nombre/{nombre}", response_model=List[Usuario])
def buscar_por_nombre(nombre: str):
    nombre_normalizado = normalizar_texto(nombre)

    try:
        # buscar usuarios cuyo nombre normalizado contenga la palabra clave
        usuarios_ref = (
            db.collection("usuarios")
            .stream()
        )

        usuarios = []
        for user in usuarios_ref:
            user_data = user.to_dict()
            user_data["fecha_nacimiento"] = date.fromisoformat(user_data["fecha_nacimiento"])
            
            # comparar el nombre normalizado con la palabra clave
            if nombre_normalizado in user_data.get("nombre_normalizado", ""):
                usuarios.append(Usuario(**user_data))

        if not usuarios:
            raise HTTPException(status_code=404, detail="no se encontraron usuarios con ese nombre")

        return usuarios

    except HTTPException as http_exc:
        # re-lanzar excepciones http ya controladas
        raise http_exc

    except Exception as e:
        # manejar errores inesperados
        raise HTTPException(status_code=500, detail="error interno del servidor")

# endpoint para obtener todos los usuarios
@app.get("/usuarios", response_model=List[Usuario])
def obtener_todos_los_usuarios():
    usuarios_ref = db.collection("usuarios").stream()
    usuarios = []

    for user in usuarios_ref:
        user_data = user.to_dict()
        user_data["fecha_nacimiento"] = date.fromisoformat(user_data["fecha_nacimiento"])
        usuarios.append(Usuario(**user_data))

    if not usuarios:
        raise HTTPException(status_code=404, detail="no hay usuarios registrados")

    return usuarios

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
            mensajes.append("error en el campo email: valor no valido. asegurate de escribir el email bien.")
        # personalizar el mensaje para el campo "fecha_nacimiento"
        elif campo == "fecha_nacimiento":
            mensajes.append("error en el campo fecha de nacimiento: la fecha no puede ser hoy ni en el futuro.")
        else:
            mensajes.append(f"error en '{campo}': {mensaje}")

    return JSONResponse(
        status_code=422,
        content={"detail": mensajes},
    )