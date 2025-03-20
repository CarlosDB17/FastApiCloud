from fastapi import FastAPI, HTTPException, Query
from config import db  # importamos la conexion a firestore
from pydantic import BaseModel
from datetime import date
from typing import List, Optional
from fastapi.middleware.cors import CORSMiddleware

import unicodedata


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

# modelo de usuario con fecha de nacimiento en formato date
class Usuario(BaseModel):
    nombre: str
    email: str
    dni: str
    fecha_nacimiento: date  

# modelo de usuario con datos opcionales para actualizacion parcial
class UsuarioUpdate(BaseModel):
    nombre: Optional[str] = None
    email: Optional[str] = None
    fecha_nacimiento: Optional[date] = None

# endpoint para registrar un nuevo usuario
@app.post("/usuarios", response_model=dict)
def registrar_usuario(usuario: Usuario):
    usuario_ref = db.collection("usuarios").document(usuario.dni)

    # verificar si el usuario ya existe
    if usuario_ref.get().exists:
        raise HTTPException(status_code=400, detail="el usuario ya existe")

    # convertimos la fecha a string porque firestore no admite el tipo date
    usuario_dict = usuario.model_dump()
    usuario_dict["fecha_nacimiento"] = usuario.fecha_nacimiento.strftime("%Y-%m-%d")
    
    # guardamos el nombre normalizado (sin acentos y en minusculas) para mejorar busquedas
    usuario_dict["nombre_normalizado"] = normalizar_texto(usuario.nombre)

    # guardamos tambien el nombre en minusculas para consultas exactas sin mayusculas
    usuario_dict["nombre_minusculas"] = usuario.nombre.lower()

    # guardar en firestore
    usuario_ref.set(usuario_dict)
    
    return {"message": "usuario registrado correctamente", "usuario": usuario_dict}

# endpoint para obtener un usuario por su dni
@app.get("/usuarios/{dni}", response_model=Usuario)
def obtener_usuario(dni: str):
    usuario_doc = db.collection("usuarios").document(dni).get()

    if not usuario_doc.exists:
        raise HTTPException(status_code=404, detail="usuario no encontrado")

    usuario_data = usuario_doc.to_dict()
    usuario_data["fecha_nacimiento"] = date.fromisoformat(usuario_data["fecha_nacimiento"])
    return Usuario(**usuario_data)

# endpoint para actualizar un usuario por su dni
@app.patch("/usuarios/{dni}", response_model=dict)
def actualizar_usuario_parcial(dni: str, usuario: UsuarioUpdate):
    usuario_ref = db.collection("usuarios").document(dni)

    if not usuario_ref.get().exists:
        raise HTTPException(status_code=404, detail="usuario no encontrado")

    update_data = usuario.model_dump(exclude_unset=True)  # excluir campos no enviados

    # si se envia "nombre", actualizar tambien las versiones normalizadas
    if "nombre" in update_data:
        update_data["nombre_normalizado"] = normalizar_texto(update_data["nombre"])
        update_data["nombre_minusculas"] = update_data["nombre"].lower()

    # si se envia "fecha_nacimiento", convertir a string
    if "fecha_nacimiento" in update_data:
        update_data["fecha_nacimiento"] = update_data["fecha_nacimiento"].strftime("%Y-%m-%d")

    usuario_ref.update(update_data)  # solo actualiza los campos enviados
    return {"message": "usuario actualizado correctamente", "actualizado": update_data}

# endpoint para eliminar un usuario por su dni
@app.delete("/usuarios/{dni}", response_model=dict)
def eliminar_usuario(dni: str):
    usuario_ref = db.collection("usuarios").document(dni)

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
        usuarios_ref = (
            db.collection("usuarios")
            .order_by("nombre_normalizado")  # buscar en la version sin acentos
            .start_at([nombre_normalizado])
            .end_at([nombre_normalizado + "\uf8ff"])
            .stream()
        )

        usuarios = []
        for user in usuarios_ref:
            user_data = user.to_dict()
            user_data["fecha_nacimiento"] = date.fromisoformat(user_data["fecha_nacimiento"])
            usuarios.append(Usuario(**user_data))

        if not usuarios:
            raise HTTPException(status_code=404, detail="no se encontraron usuarios con ese nombre")

        return usuarios

    except Exception:
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
