"""
unreal_payload.py — Envío a Unreal / E3DS  (FUTURO — TODO, no usar aún)
======================================================================
Este módulo queda COMENTADO/inactivo a propósito. Es el puente para cuando el
proyecto esté cerrado: enviar a Unreal (vía E3DS) la pareja albedo + normal por
URL, automáticamente.

Cómo lo hace el web hoy (Clothfigurator_web), que acá replicamos:
  - El albedo se manda con el payload:   { "textureURL": <albedoURL> }
  - La normal se manda con el payload:   { "textureNormalURL": <normalURL> }
  (helpers: createTextureUrlPayload / createTextureNormalUrlPayload)

Las URLs se construyen igual que el web:
  {PB_URL}/api/files/{coleccion}/{recordId}/{archivo}?token={fileToken}
  -> exactamente lo que devuelve PBAdmin.file_view_url() / el endpoint /api/pair.

Del lado de Unreal, el material PADRE expone (nombres EXACTOS dados por Juan):
  - bool / static switch param :  "Base 01 - Normal - Map"      -> hay que ponerlo en TRUE
                                   para habilitar el sample de normal.
  - texture param             :  "Base 01 - Normal - Texture"   -> recibe la normal.
  (el albedo ya es funcional y usa su propio par de parámetros, con la misma
   convención de nombres.)

------------------------------------------------------------------------
TODO (activar cuando se cierre el proyecto):

# from pocketbase import PBAdmin
#
# # Nombres de parámetros del material padre en Unreal:
# UE_NORMAL_ENABLE_PARAM = "Base 01 - Normal - Map"      # bool: habilitar sample
# UE_NORMAL_TEXTURE_PARAM = "Base 01 - Normal - Texture"  # texture: la normal
#
# def build_pair_payload(admin: PBAdmin, record_id: str, albedo: str, normal: str) -> list[dict]:
#     \"\"\"Payloads E3DS para enviar albedo + normal, en orden (primero albedo).\"\"\"
#     albedo_url = admin.file_view_url(record_id, albedo)
#     normal_url = admin.file_view_url(record_id, normal)
#     return [
#         {"textureURL": albedo_url},          # 1) albedo
#         {"textureNormalURL": normal_url},    # 2) normal generada
#     ]
#
# def build_material_param_payload(normal_url: str) -> dict:
#     \"\"\"Si en vez de textureNormalURL se setean params del material directo.\"\"\"
#     return {
#         "setMaterialParam": [
#             {"name": UE_NORMAL_ENABLE_PARAM,  "type": "bool",    "value": True},
#             {"name": UE_NORMAL_TEXTURE_PARAM, "type": "texture", "value": normal_url},
#         ]
#     }
#
# El transporte real (websocket/postMessage a la iframe E3DS, o HTTP a Unreal)
# se define cuando integremos. Por ahora /api/pair ya entrega {albedoURL, normalURL}.
------------------------------------------------------------------------
"""

# Intencionalmente sin código activo. Ver TODO arriba.
