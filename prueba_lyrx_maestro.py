# -*- coding: utf-8 -*-
"""
Prueba de validacion: construccion del lyrx maestro.

Objetivo: validar que se pueden exportar los grupos/capas de un APRX de
servicio a archivos .lyrx y reimportarlos en un APRX base dentro de un grupo
homonimo, PRESERVANDO el anidamiento de subgrupos y la simbologia.

Es una prueba AISLADA (no toca TPK-Generator). Se corre desde consola con el
interprete de ArcGIS Pro:
    "C:\\Program Files\\ArcGIS\\Pro\\bin\\Python\\envs\\arcgispro-py3\\python" prueba_lyrx_maestro.py

Verificar en el log:
  - La estructura del APRX de servicio (paso 4).
  - La estructura del APRX resultante (paso 9).
  Ambas deben coincidir en anidamiento; las capas deben conservar simbologia.
"""

import arcpy
import os
import shutil
import datetime

# ======================================================================
# CONFIGURACION (ajustar STARTUP_PATH al ambiente de prueba)
# ======================================================================
STARTUP_PATH = r"D:\gisteco\APPMovilOFFLINE"          # <- AJUSTAR
SALIDA       = STARTUP_PATH + r"\Hubs"                 # <- AJUSTAR si config['SALIDA'] difiere

TEMPLATE_DIR = STARTUP_PATH + r"\Template"
TEMP_LYRX    = TEMPLATE_DIR + r"\temp_lyrx"
PLANTILLA_BASE = TEMPLATE_DIR + r"\template_base_vacio.aprx"

APRX_SERVICIO_PRUEBA = "03_RED_HFC.aprx"               # APRX de servicio a validar
NOMBRE_MAPA = "Mapa"                                   # mapa interno (todos usan "Mapa")

APRX_RESULTADO = SALIDA + r"\PRUEBA_lyrx_maestro.aprx"  # salida de la prueba

# Exclusiones por nombre de capa (para probar el mecanismo; vacio = no excluye)
CAPAS_EXCLUIDAS_APPOFFLINE = set()
# ======================================================================


def log(msg):
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"{ts} {msg}")


def log_estructura(mapa, titulo):
    """Recorre el mapa y loguea la jerarquia completa (grupos, subgrupos,
    capas) con indentacion por nivel y la simbologia de cada capa."""
    log("=" * 70)
    log(f"ESTRUCTURA: {titulo}")
    log("=" * 70)
    for lyr in mapa.listLayers():
        # longIndex indica el nivel de anidamiento (cantidad de segmentos)
        try:
            nivel = lyr.longName.count("\\")
        except Exception:
            nivel = 0
        sangria = "    " * nivel
        if lyr.isGroupLayer:
            log(f"{sangria}[GRUPO] {lyr.name}  (longName: {lyr.longName})")
        else:
            simbologia = "?"
            try:
                if lyr.supports("SYMBOLOGY"):
                    sym = lyr.symbology
                    simbologia = type(sym.renderer).__name__ if hasattr(sym, "renderer") else "sin renderer"
                else:
                    simbologia = "no soporta SYMBOLOGY"
            except Exception as e:
                simbologia = f"err:{e}"
            roto = ""
            fuente = ""
            try:
                if lyr.supports("DATASOURCE"):
                    if lyr.isBroken:
                        roto = " [FUENTE ROTA]"
                        fuente = " -> (inaccesible)"
                    else:
                        try:
                            fuente = f" -> {lyr.dataSource}"
                        except Exception as e:
                            fuente = f" -> (error dataSource: {e})"
            except Exception as e:
                roto = f" [error isBroken: {e}]"
            log(f"{sangria}[CAPA]  {lyr.name}  | simbologia: {simbologia}{roto}{fuente}")
    log("=" * 70)


def main():
    log("#" * 70)
    log("INICIO PRUEBA - construccion lyrx maestro")
    log("#" * 70)

    arcpy.env.overwriteOutput = True

    # ------------------------------------------------------------------
    # Validaciones previas de existencia
    # ------------------------------------------------------------------
    ruta_servicio = os.path.join(TEMPLATE_DIR, APRX_SERVICIO_PRUEBA)
    for ruta in (TEMPLATE_DIR, PLANTILLA_BASE, ruta_servicio):
        if not os.path.exists(ruta):
            raise RuntimeError(f"No existe la ruta requerida: {ruta}")
    log(f"[OK] Template          : {TEMPLATE_DIR}")
    log(f"[OK] Plantilla base    : {PLANTILLA_BASE}")
    log(f"[OK] APRX de servicio  : {ruta_servicio}")

    # ------------------------------------------------------------------
    # PASO 3 - Purga previa de temp_lyrx (subcarpeta del APRX de prueba)
    # ------------------------------------------------------------------
    nombre_sin_ext = os.path.splitext(APRX_SERVICIO_PRUEBA)[0]   # 03_RED_HFC
    carpeta_lyrx = os.path.join(TEMP_LYRX, nombre_sin_ext)
    if os.path.exists(carpeta_lyrx):
        shutil.rmtree(carpeta_lyrx)
        log(f"[PURGA] Eliminada carpeta previa: {carpeta_lyrx}")
    os.makedirs(carpeta_lyrx, exist_ok=True)
    log(f"[OK] Carpeta temp_lyrx lista: {carpeta_lyrx}")

    # ------------------------------------------------------------------
    # PASO 4 - Inspeccion de la estructura del APRX de servicio
    # ------------------------------------------------------------------
    aprx_servicio = arcpy.mp.ArcGISProject(ruta_servicio)
    mapa_servicio = aprx_servicio.listMaps(NOMBRE_MAPA)[0]
    log_estructura(mapa_servicio, f"ORIGEN - {APRX_SERVICIO_PRUEBA}")

    # ------------------------------------------------------------------
    # PASO 5 - Exportar a .lyrx cada elemento de PRIMER NIVEL
    #   Un grupo exportado arrastra su arbol completo (subgrupos + simbologia).
    #   Una capa suelta se exporta individualmente.
    # ------------------------------------------------------------------
    lyrx_generados = []   # lista de (ruta_lyrx, es_grupo, nombre)

    # listLayers da todo aplanado; para "primer nivel" filtramos por longName
    # sin separador de jerarquia (los de primer nivel no tienen "\" en longName)
    elementos_primer_nivel = [
        lyr for lyr in mapa_servicio.listLayers()
        if "\\" not in lyr.longName
    ]
    log(f"[INFO] Elementos de primer nivel detectados: {len(elementos_primer_nivel)}")

    for lyr in elementos_primer_nivel:
        # Exclusion por nombre (aplica a capas; para grupos, se evalua el nombre del grupo)
        if lyr.name in CAPAS_EXCLUIDAS_APPOFFLINE:
            log(f"[EXCLUIDO] {lyr.name} (en CAPAS_EXCLUIDAS_APPOFFLINE)")
            continue

        destino = os.path.join(carpeta_lyrx, f"{lyr.name}.lyrx")
        try:
            # Metodo nativo arcpy.mp: preserva simbologia y, si es grupo,
            # guarda todo el arbol de subcapas. Produce un .lyrx compatible
            # con arcpy.mp.LayerFile (SaveToLayerFile GP no lo es).
            lyr.saveACopy(destino)
            tipo = "GRUPO" if lyr.isGroupLayer else "CAPA"
            # Verificar que el archivo se creo y tiene contenido
            if os.path.exists(destino) and os.path.getsize(destino) > 0:
                tam = os.path.getsize(destino)
                log(f"[EXPORT lyrx] [{tipo}] {lyr.name} -> {destino} ({tam} bytes)")
                lyrx_generados.append((destino, lyr.isGroupLayer, lyr.name))
            else:
                log(f"[ERROR export] {lyr.name}: archivo no creado o vacio")
        except Exception as e:
            log(f"[ERROR export] {lyr.name}: repr={repr(e)}")

    if not lyrx_generados:
        raise RuntimeError("No se exporto ningun .lyrx; se aborta la prueba.")

    # ------------------------------------------------------------------
    # PASO 6 - Preparar APRX base (copiar plantilla vacia y abrir la copia)
    #   arcpy.mp NO crea proyectos vacios en memoria: se parte de la plantilla.
    # ------------------------------------------------------------------
    if os.path.exists(APRX_RESULTADO):
        os.remove(APRX_RESULTADO)
        log(f"[LIMPIEZA] Eliminado resultado previo: {APRX_RESULTADO}")
    shutil.copyfile(PLANTILLA_BASE, APRX_RESULTADO)
    log(f"[OK] Plantilla base copiada a: {APRX_RESULTADO}")

    aprx_base = arcpy.mp.ArcGISProject(APRX_RESULTADO)
    mapa_base = aprx_base.listMaps(NOMBRE_MAPA)[0]
    log(f"[OK] APRX base abierto, mapa '{NOMBRE_MAPA}' obtenido")

    # ------------------------------------------------------------------
    # PASO 7 - Crear grupo contenedor NN_nombre y reimportar los .lyrx dentro
    # ------------------------------------------------------------------
    nombre_grupo = nombre_sin_ext   # 03_RED_HFC
    log(f"[GRUPO] Creando grupo contenedor: {nombre_grupo}")

    # Crear un group layer vacio a partir de un .lyrx de grupo temporal.
    # arcpy.mp no tiene "createGroupLayer" directo; se crea insertando un
    # group layer. Estrategia: crear el grupo usando createGroupLayer si esta
    # disponible (Pro 2.5+), si no, fallback documentado.
    grupo_contenedor = None
    try:
        # Disponible en ArcGIS Pro 2.5+
        grupo_contenedor = mapa_base.createGroupLayer(nombre_grupo)
        log(f"[OK] Grupo contenedor creado con createGroupLayer: {nombre_grupo}")
    except Exception as e:
        log(f"[WARNING] createGroupLayer no disponible o fallo: {e}")
        log("[INFO] Fallback: se importaran los .lyrx al mapa y se agrupan luego")

    # Reimportar cada .lyrx dentro del grupo contenedor.
    # Se prueban DOS metodos: si el A falla, se intenta el B automaticamente,
    # para identificar en una sola corrida cual funciona en este entorno.
    import traceback
    for ruta_lyrx, es_grupo, nombre in lyrx_generados:
        lyr_file = arcpy.mp.LayerFile(ruta_lyrx)

        # --- Metodo A: addLayerToGroup(grupo, layerfile) ---
        ok = False
        try:
            resultado = mapa_base.addLayerToGroup(grupo_contenedor, lyr_file)
            log(f"[IMPORT-A] {nombre} -> grupo {nombre_grupo} OK (retorno: {resultado})")
            ok = True
        except Exception as e:
            log(f"[IMPORT-A FALLO] {nombre}: repr={repr(e)}")
            log(f"[TRACEBACK-A] {traceback.format_exc().strip().splitlines()[-1]}")

        if ok:
            continue

        # --- Metodo B: addLayer al mapa + mover al grupo con addLayerToGroup ---
        # Patron alternativo: primero se agrega el grupo al mapa (raiz),
        # luego se mueve dentro del contenedor. Referencia: Esri Community.
        try:
            agregados = mapa_base.addLayer(lyr_file)   # devuelve lista de capas agregadas
            log(f"[IMPORT-B] {nombre} agregado a raiz del mapa ({len(agregados)} elem)")
            # Mover cada elemento agregado dentro del grupo contenedor
            for capa_ag in agregados:
                try:
                    mapa_base.addLayerToGroup(grupo_contenedor, capa_ag)
                    mapa_base.removeLayer(capa_ag)
                    log(f"[IMPORT-B] {capa_ag.name} movido a grupo {nombre_grupo}")
                except Exception as e2:
                    log(f"[IMPORT-B mover FALLO] {capa_ag.name}: repr={repr(e2)}")
        except Exception as e:
            log(f"[IMPORT-B FALLO] {nombre}: repr={repr(e)}")
            log(f"[TRACEBACK-B] {traceback.format_exc().strip().splitlines()[-1]}")

    # ------------------------------------------------------------------
    # PASO 8 - Guardar el APRX resultante
    # ------------------------------------------------------------------
    aprx_base.save()
    log(f"[OK] APRX resultante guardado: {APRX_RESULTADO}")

    # ------------------------------------------------------------------
    # PASO 9 - Re-inspeccionar el resultado y verificar preservacion
    # ------------------------------------------------------------------
    aprx_verif = arcpy.mp.ArcGISProject(APRX_RESULTADO)
    mapa_verif = aprx_verif.listMaps(NOMBRE_MAPA)[0]
    log_estructura(mapa_verif, "RESULTADO - PRUEBA_lyrx_maestro.aprx")

    log("#" * 70)
    log("FIN PRUEBA - comparar ESTRUCTURA ORIGEN (paso 4) vs RESULTADO (paso 9)")
    log("Verificar: mismo anidamiento de grupos/subgrupos y simbologia preservada")
    log("#" * 70)


if __name__ == "__main__":
    main()
