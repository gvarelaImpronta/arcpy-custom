# -*- coding: utf-8 -*-
"""
Prueba de comportamiento ANTE CAPA ROTA.

Objetivo: determinar que pasa cuando un APRX de servicio tiene capas con
fuente de datos inaccesible (rota) al intentar exportarlas a .lyrx y
reimportarlas. Es el escenario esperado en PROD si un APRX apunta a una DB
sin acceso.

Metodo: trabaja sobre una COPIA de 03_RED_HFC.aprx, le rompe la fuente de las
capas reapuntando a un workspace inexistente, y observa:
  - Si las capas quedan isBroken=True.
  - Si saveACopy del grupo con capas rotas falla o genera el .lyrx igual.
  - Si el .lyrx generado se puede reabrir.
  - Si al reimportar, el grupo entra con las capas rotas marcadas.

NO modifica el APRX original (trabaja sobre copia).

Correr desde consola:
    "C:\\Program Files\\ArcGIS\\Pro\\bin\\Python\\envs\\arcgispro-py3\\python" prueba_lyrx_capa_rota.py
"""

import arcpy
import os
import shutil
import datetime
import traceback

# ======================================================================
# CONFIGURACION (mismas rutas que la prueba principal)
# ======================================================================
STARTUP_PATH = r"D:\gisteco\APPMovilOFFLINE"          # <- AJUSTAR
SALIDA       = STARTUP_PATH + r"\Hubs"                 # <- AJUSTAR si difiere

TEMPLATE_DIR = STARTUP_PATH + r"\Template"
TEMP_LYRX    = TEMPLATE_DIR + r"\temp_lyrx"
PLANTILLA_BASE = TEMPLATE_DIR + r"\template_base_vacio.aprx"

APRX_SERVICIO = "03_RED_HFC.aprx"
NOMBRE_MAPA = "Mapa"

# Copia de trabajo del APRX (para romperle la fuente sin tocar el original)
APRX_COPIA_ROTA = SALIDA + r"\03_RED_HFC_ROTO.aprx"
APRX_RESULTADO  = SALIDA + r"\PRUEBA_capa_rota_resultado.aprx"

# Workspace inexistente al que se reapuntan las capas para romperlas
WORKSPACE_INEXISTENTE = r"D:\NO_EXISTE\conexion_rota.sde"
# ======================================================================


def log(msg):
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"{ts} {msg}")


def main():
    log("#" * 70)
    log("PRUEBA - comportamiento ante CAPA ROTA")
    log("#" * 70)

    arcpy.env.overwriteOutput = True

    ruta_servicio = os.path.join(TEMPLATE_DIR, APRX_SERVICIO)
    if not os.path.exists(ruta_servicio):
        raise RuntimeError(f"No existe: {ruta_servicio}")

    # ------------------------------------------------------------------
    # 1) Copiar el APRX de servicio a una copia de trabajo
    # ------------------------------------------------------------------
    if os.path.exists(APRX_COPIA_ROTA):
        os.remove(APRX_COPIA_ROTA)
    shutil.copyfile(ruta_servicio, APRX_COPIA_ROTA)
    log(f"[OK] Copia de trabajo creada: {APRX_COPIA_ROTA}")

    # ------------------------------------------------------------------
    # 2) Romper la fuente: reapuntar las capas a un workspace inexistente
    # ------------------------------------------------------------------
    aprx_roto = arcpy.mp.ArcGISProject(APRX_COPIA_ROTA)
    mapa_roto = aprx_roto.listMaps(NOMBRE_MAPA)[0]

    log("[INFO] Intentando romper fuentes con findAndReplaceWorkspacePath...")
    try:
        # Reapunta cualquier workspace del mapa al inexistente.
        # validate=False permite setear una ruta que no existe.
        mapa_roto.findAndReplaceWorkspacePath("", WORKSPACE_INEXISTENTE, validate=False)
        log("[OK] findAndReplaceWorkspacePath aplicado")
    except Exception as e:
        log(f"[WARNING] findAndReplaceWorkspacePath fallo: {repr(e)}")
        log("[INFO] Intentando por capa con updateConnectionProperties...")
        for lyr in mapa_roto.listLayers():
            if not lyr.isGroupLayer and lyr.supports("CONNECTIONPROPERTIES"):
                try:
                    lyr.updateConnectionProperties(
                        lyr.connectionProperties,
                        {"connection_info": {"database": WORKSPACE_INEXISTENTE}},
                        validate=False
                    )
                except Exception as e2:
                    log(f"  [WARN] {lyr.name}: {repr(e2)}")

    aprx_roto.save()
    log("[OK] Copia con fuentes modificadas guardada")

    # ------------------------------------------------------------------
    # 3) Verificar estado de las capas (deberian estar rotas)
    # ------------------------------------------------------------------
    aprx_roto = arcpy.mp.ArcGISProject(APRX_COPIA_ROTA)
    mapa_roto = aprx_roto.listMaps(NOMBRE_MAPA)[0]
    log("=" * 70)
    log("ESTADO DE CAPAS TRAS ROMPER FUENTE")
    log("=" * 70)
    rotas = 0
    total = 0
    for lyr in mapa_roto.listLayers():
        if lyr.isGroupLayer:
            log(f"[GRUPO] {lyr.name}")
            continue
        total += 1
        try:
            estado = "ROTA" if lyr.isBroken else "OK"
            if lyr.isBroken:
                rotas += 1
        except Exception as e:
            estado = f"err:{e}"
        log(f"    [CAPA] {lyr.name}: {estado}")
    log(f"[RESUMEN] {rotas}/{total} capas rotas")
    log("=" * 70)

    # ------------------------------------------------------------------
    # 4) Intentar exportar un grupo con capas rotas a .lyrx
    # ------------------------------------------------------------------
    carpeta_lyrx = os.path.join(TEMP_LYRX, "03_RED_HFC_ROTO")
    if os.path.exists(carpeta_lyrx):
        shutil.rmtree(carpeta_lyrx)
    os.makedirs(carpeta_lyrx, exist_ok=True)

    grupos_primer_nivel = [
        lyr for lyr in mapa_roto.listLayers()
        if "\\" not in lyr.longName and lyr.isGroupLayer
    ]
    log(f"[INFO] Grupos de primer nivel a exportar: {[g.name for g in grupos_primer_nivel]}")

    lyrx_ok = []
    for grupo in grupos_primer_nivel:
        destino = os.path.join(carpeta_lyrx, f"{grupo.name}.lyrx")
        log(f"[EXPORT] Intentando saveACopy de grupo ROTO: {grupo.name}")
        try:
            grupo.saveACopy(destino)
            if os.path.exists(destino) and os.path.getsize(destino) > 0:
                log(f"  [OK] .lyrx generado pese a capas rotas: {destino} "
                    f"({os.path.getsize(destino)} bytes)")
                lyrx_ok.append((destino, grupo.name))
            else:
                log(f"  [FALLO] .lyrx no generado o vacio")
        except Exception as e:
            log(f"  [EXCEPCION en saveACopy] repr={repr(e)}")
            log(f"  [TRACEBACK] {traceback.format_exc().strip().splitlines()[-1]}")

    # ------------------------------------------------------------------
    # 5) Intentar reabrir y reimportar los .lyrx rotos
    # ------------------------------------------------------------------
    if not lyrx_ok:
        log("[CONCLUSION] Con capas rotas, NO se genero ningun .lyrx.")
        log("             => saveACopy falla ante fuente rota. El try/except")
        log("                por APRX saltearia ese APRX en el flujo real.")
        return

    log("[INFO] Preparando APRX base para reimportar los .lyrx rotos...")
    if os.path.exists(APRX_RESULTADO):
        os.remove(APRX_RESULTADO)
    shutil.copyfile(PLANTILLA_BASE, APRX_RESULTADO)
    aprx_base = arcpy.mp.ArcGISProject(APRX_RESULTADO)
    mapa_base = aprx_base.listMaps(NOMBRE_MAPA)[0]
    grupo_cont = mapa_base.createGroupLayer("03_RED_HFC_ROTO")

    for ruta_lyrx, nombre in lyrx_ok:
        try:
            lf = arcpy.mp.LayerFile(ruta_lyrx)
            mapa_base.addLayerToGroup(grupo_cont, lf, "BOTTOM")
            log(f"[IMPORT] {nombre} reimportado (con capas rotas)")
        except Exception as e:
            log(f"[IMPORT FALLO] {nombre}: repr={repr(e)}")

    aprx_base.save()
    log(f"[OK] APRX resultante guardado: {APRX_RESULTADO}")

    # Verificar estado de las capas reimportadas
    aprx_v = arcpy.mp.ArcGISProject(APRX_RESULTADO)
    mapa_v = aprx_v.listMaps(NOMBRE_MAPA)[0]
    log("=" * 70)
    log("ESTADO DE CAPAS REIMPORTADAS")
    log("=" * 70)
    for lyr in mapa_v.listLayers():
        if lyr.isGroupLayer:
            log(f"[GRUPO] {lyr.name} (longName: {lyr.longName})")
        else:
            try:
                estado = "ROTA" if lyr.isBroken else "OK"
            except Exception as e:
                estado = f"err:{e}"
            log(f"    [CAPA] {lyr.name}: {estado}")
    log("=" * 70)
    log("[CONCLUSION] saveACopy SI genera .lyrx con capas rotas; las capas")
    log("             se reimportan marcadas como rotas. Decidir si validar")
    log("             isBroken antes de incluir el APRX en el maestro.")
    log("#" * 70)


if __name__ == "__main__":
    main()
