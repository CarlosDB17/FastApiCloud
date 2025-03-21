from fastapi import FastAPI, HTTPException, Query
from config import db  # importamos la conexion a firestore
from pydantic import BaseModel, EmailStr, field_validator
from datetime import date
from typing import List, Optional
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi import Request

import unicodedata
import re  # Para validar el DNI


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

# funcion para validar el DNI español
def validar_dni(dni: str) -> bool:
    patron = r"^\d{8}[A-HJ-NP-TV-Z]$"
    letras = "TRWAGMYFPDXBNJZSQVHLCKE"
    if re.match(patron, dni):
        numero = int(dni[:-1])
        letra = dni[-1].upper()
        return letras[numero % 23] == letra
    return False

# modelo de usuario con validaciones
class Usuario(BaseModel):
    nombre: str
    email: EmailStr
    dni: str
    fecha_nacimiento: date

    @field_validator("dni")
    def validar_dni_espanol(cls, dni):
        if not validar_dni(dni):
            raise ValueError("El DNI no es válido")
        return dni

    @field_validator("fecha_nacimiento")
    def validar_fecha_nacimiento(cls, fecha):
        if fecha >= date.today():
            raise ValueError("La fecha de nacimiento no puede ser hoy ni en el futuro")
        return fecha

# modelo de usuario con datos opcionales para actualizacion parcial
class UsuarioUpdate(BaseModel):
    nombre: Optional[str] = None
    email: Optional[EmailStr] = None
    fecha_nacimiento: Optional[date] = None

    @field_validator("fecha_nacimiento", mode="before")
    def validar_fecha_nacimiento(cls, fecha):
        if fecha and fecha >= date.today():
            raise ValueError("La fecha de nacimiento no puede ser hoy ni en el futuro")
        return fecha

# endpoint para registrar un nuevo usuario
@app.post("/usuarios", response_model=dict)
def registrar_usuario(usuario: Usuario):
    usuario_ref = db.collection("usuarios").document(usuario.dni)

    # verificar si el usuario ya existe por DNI
    if usuario_ref.get().exists:
        raise HTTPException(status_code=400, detail="Este DNI ya ha sido registrado")

    # verificar si el email ya existe
    email_ref = db.collection("usuarios").where("email", "==", usuario.email).get()
    if email_ref:
        raise HTTPException(status_code=400, detail="Este email ya ha sido registrado")

    # convertir la fecha a string porque Firestore no admite el tipo date
    usuario_dict = usuario.model_dump()
    usuario_dict["fecha_nacimiento"] = usuario.fecha_nacimiento.strftime("%Y-%m-%d")

    # guardar el nombre normalizado (sin acentos y en minusculas) para mejorar busquedas
    usuario_dict["nombre_normalizado"] = normalizar_texto(usuario.nombre)

    # guardar también el nombre en minusculas para consultas exactas sin mayusculas
    usuario_dict["nombre_minusculas"] = usuario.nombre.lower()

    # guardar en Firestore
    usuario_ref.set(usuario_dict)

    return {"message": "Usuario registrado correctamente", "usuario": usuario_dict}

# endpoint para obtener un usuario por su DNI
@app.get("/usuarios/{dni}", response_model=Usuario)
def obtener_usuario(dni: str):
    usuario_doc = db.collection("usuarios").document(dni).get()

    if not usuario_doc.exists:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    usuario_data = usuario_doc.to_dict()
    usuario_data["fecha_nacimiento"] = date.fromisoformat(usuario_data["fecha_nacimiento"])
    return Usuario(**usuario_data)

# endpoint para actualizar un usuario por su DNI
@app.patch("/usuarios/{dni}", response_model=dict)
def actualizar_usuario_parcial(dni: str, usuario: UsuarioUpdate):
    usuario_ref = db.collection("usuarios").document(dni)

    if not usuario_ref.get().exists:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    update_data = usuario.model_dump(exclude_unset=True)  # excluir campos no enviados

    # si se envía "nombre", actualizar también las versiones normalizadas
    if "nombre" in update_data:
        update_data["nombre_normalizado"] = normalizar_texto(update_data["nombre"])
        update_data["nombre_minusculas"] = update_data["nombre"].lower()

    # si se envía "fecha_nacimiento", convertir a string
    if "fecha_nacimiento" in update_data:
        update_data["fecha_nacimiento"] = update_data["fecha_nacimiento"].strftime("%Y-%m-%d")

    # si se envía "email", verificar duplicados
    if "email" in update_data:
        email_ref = db.collection("usuarios").where("email", "==", update_data["email"]).get()
        if email_ref:
            raise HTTPException(status_code=400, detail="Este email ya ha sido registrado")

    usuario_ref.update(update_data)  # solo actualiza los campos enviados
    return {"message": "Usuario actualizado correctamente", "actualizado": update_data}

# endpoint para eliminar un usuario por su DNI
@app.delete("/usuarios/{dni}", response_model=dict)
def eliminar_usuario(dni: str):
    usuario_ref = db.collection("usuarios").document(dni)

    if not usuario_ref.get().exists:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    usuario_ref.delete()
    return {"message": "Usuario eliminado correctamente"}

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
        # Buscar usuarios cuyo nombre normalizado contenga la palabra clave
        usuarios_ref = (
            db.collection("usuarios")
            .stream()
        )

        usuarios = []
        for user in usuarios_ref:
            user_data = user.to_dict()
            user_data["fecha_nacimiento"] = date.fromisoformat(user_data["fecha_nacimiento"])
            
            # Comparar el nombre normalizado con la palabra clave
            if nombre_normalizado in user_data.get("nombre_normalizado", ""):
                usuarios.append(Usuario(**user_data))

        if not usuarios:
            raise HTTPException(status_code=404, detail="No se encontraron usuarios con ese nombre")

        return usuarios

    except Exception as e:
        # manejar errores de manera más clara
        raise HTTPException(status_code=500, detail=f"Error interno del servidor: {str(e)}")

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
        campo = ".".join(error["loc"][1:])  # pbtener el campo que fallo
        mensaje = error["msg"]  # Mensaje de error

        # personalizar el mensaje para el campo "email"
        if campo == "email":
            mensajes.append("Error en el campo Email: valor no válido. Asegúrate de escribir el email bien.")
        # personalizar el mensaje para el campo "fecha_nacimiento"
        elif campo == "fecha_nacimiento":
            mensajes.append("Error en el campo Fecha de Nacimiento: La fecha no puede ser hoy ni en el futuro.")
        else:
            mensajes.append(f"Error en '{campo}': {mensaje}")

    return JSONResponse(
        status_code=422,
        content={"detail": mensajes},
    )
