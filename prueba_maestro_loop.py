# -*- coding: utf-8 -*-
"""
Prueba del modulo construir_lyrx_maestro (loop completo).

Ejercita la construccion del lyrx maestro tal como se usara en TPK-Generator:
descubrimiento de APRX de servicio, loop, deteccion proactiva de capas rotas,
exclusiones, resumen y subtipo de error de TPK.

Correr desde consola (mismo directorio que construir_lyrx_maestro.py):
    "C:\\Program Files\\ArcGIS\\Pro\\bin\\Python\\envs\\arcgispro-py3\\python" prueba_maestro_loop.py
"""

import os
import datetime
from construir_lyrx_maestro import construir_lyrx_maestro, preparar_aprx_region

# ======================================================================
# CONFIGURACION (ajustar al ambiente)
# ======================================================================
STARTUP_PATH = r"D:\gisteco\APPMovilOFFLINE"       # <- AJUSTAR
SALIDA       = STARTUP_PATH + r"\Hubs"             # <- AJUSTAR si difiere

TEMPLATE_DIR   = STARTUP_PATH + r"\Template"
PLANTILLA_BASE = TEMPLATE_DIR + r"\template_base_vacio.aprx"
NOMBRE_MAPA    = "Mapa"
APRX_MAESTRO   = SALIDA + r"\APRX_MAESTRO.aprx"

# Exclusiones por nombre de capa (vacio = no excluye). Ejemplo para probar:
# CAPAS_EXCLUIDAS_APPOFFLINE = {"FOAM", "TERMINALES"}
CAPAS_EXCLUIDAS_APPOFFLINE = set()

# Para probar preparar_aprx_region: TPK VRED existente y nombre de region.
TPK_VRED_PRUEBA = SALIDA + r"\TPK_11 CAPITAL NORTE_VRED.tpk"
REGION_PRUEBA   = "11 CAPITAL NORTE"
# ======================================================================


def log(msg):
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"{ts} {msg}")


def main():
    log("#" * 70)
    log("PRUEBA - construir_lyrx_maestro (loop completo)")
    log("#" * 70)

    ruta_maestro, subtipo, resumen = construir_lyrx_maestro(
        template_dir=TEMPLATE_DIR,
        salida_dir=SALIDA,
        plantilla_base=PLANTILLA_BASE,
        capas_excluidas=CAPAS_EXCLUIDAS_APPOFFLINE,
        nombre_mapa=NOMBRE_MAPA,
        aprx_maestro_salida=APRX_MAESTRO,
        log=log,
    )

    log("#" * 70)
    log(f"RESULTADO: maestro={ruta_maestro}")
    log(f"           subtipo_error_tpk={subtipo}  (0=OK, 2=capa rota)")
    log(f"           capas_rotas={len(resumen['capas_rotas'])}")
    log(f"           grupos_creados={resumen['grupos_creados']}")
    log(f"           reference_scale=1:{resumen.get('reference_scale')}")
    log("#" * 70)

    # Verificacion final: inspeccionar la estructura del maestro generado
    import arcpy
    log("=" * 70)
    log("ESTRUCTURA DEL MAESTRO GENERADO")
    log("=" * 70)
    aprx = arcpy.mp.ArcGISProject(ruta_maestro)
    mapa = aprx.listMaps(NOMBRE_MAPA)[0]
    # Reference scale del mapa maestro (debe coincidir con la del servicio, 1:1000)
    try:
        log(f"Reference scale del mapa maestro: 1:{mapa.referenceScale}")
    except Exception as e:
        log(f"Reference scale: (no disponible) {e}")
    for lyr in mapa.listLayers():
        nivel = lyr.longName.count("\\")
        sangria = "    " * nivel
        if lyr.isGroupLayer:
            log(f"{sangria}[GRUPO] {lyr.name}")
        else:
            roto = ""
            try:
                if lyr.supports("DATASOURCE") and lyr.isBroken:
                    roto = " [ROTA]"
            except Exception:
                pass
            log(f"{sangria}[CAPA] {lyr.name}{roto}")
    log("=" * 70)

    # ------------------------------------------------------------------
    # Prueba de preparar_aprx_region (maestro + TPK VRED al fondo)
    # ------------------------------------------------------------------
    log("#" * 70)
    log("PRUEBA - preparar_aprx_region")
    log("#" * 70)
    if not os.path.exists(TPK_VRED_PRUEBA):
        log(f"[SALTEADO] No existe el TPK VRED de prueba: {TPK_VRED_PRUEBA}")
    else:
        ruta_region = preparar_aprx_region(
            aprx_maestro=ruta_maestro,
            salida_dir=SALIDA,
            region=REGION_PRUEBA,
            tpk_path_VRED_BASEMAP=TPK_VRED_PRUEBA,
            nombre_mapa=NOMBRE_MAPA,
            log=log,
        )
        log(f"[RESULTADO] APRX region generado: {ruta_region}")

        # Inspeccionar la estructura del APRX de region
        log("=" * 70)
        log(f"ESTRUCTURA DEL APRX_{REGION_PRUEBA} (maestro + TPK VRED)")
        log("=" * 70)
        aprx_r = arcpy.mp.ArcGISProject(ruta_region)
        mapa_r = aprx_r.listMaps(NOMBRE_MAPA)[0]
        try:
            log(f"Reference scale del APRX region: 1:{mapa_r.referenceScale}")
        except Exception as e:
            log(f"Reference scale: (no disponible) {e}")
        for lyr in mapa_r.listLayers():
            nivel = lyr.longName.count("\\")
            sangria = "    " * nivel
            if lyr.isGroupLayer:
                log(f"{sangria}[GRUPO] {lyr.name}")
            else:
                roto = ""
                try:
                    if lyr.supports("DATASOURCE") and lyr.isBroken:
                        roto = " [ROTA]"
                except Exception:
                    pass
                log(f"{sangria}[CAPA] {lyr.name}{roto}")
        log("=" * 70)
        log(">> Verificar: el TPK VRED (mapa base) debe aparecer AL FONDO del TOC,")
        log(">> los grupos de servicio arriba, y la reference scale conservada (1:1000).")


if __name__ == "__main__":
    main()
