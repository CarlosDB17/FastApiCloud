import os
import json
import base64

import firebase_admin
from firebase_admin import credentials, firestore

# leer la clave json desde la variable de entorno
clave_json_base64 = os.getenv("GOOGLE_CREDENTIALS")

if clave_json_base64:
    # decodificar y cargar como diccionario
    clave_json = json.loads(base64.b64decode(clave_json_base64).decode("utf-8"))

    # inicializar firebase con la clave decodificada
    cred = credentials.Certificate(clave_json)
    firebase_admin.initialize_app(cred)

    # conectar con firestore
    db = firestore.client()
else:
    raise ValueError("no se encontro GOOGLE_CREDENTIALS en las variables de entorno")

__all__ = ["db"]