# -*- coding: utf-8 -*-
"""
TPK-Generator — Refactor con armado dinamico del TPK via lyrx maestro.

Genera, por cada subregion:
  - un TPK de mapa base VRED (recortado a las areas de hub de la subregion), y
  - un APRX_{region} que combina el lyrx maestro de capas de servicio (armado
    dinamicamente desde los APRX de la carpeta Template) con ese TPK VRED al
    fondo. El MMPK-Generator (script aparte) consume esos APRX_{region}.

ARQUITECTURA DE ERRORES (decidida en diseno):
  - El catalogo de codigos (ErrorTPK) y la excepcion (ErrorTPKException) son de
    NIVEL MODULO: los comparten construir_lyrx_maestro (que detecta sus errores)
    y main (que detecta el error de teselas y compone el codigo final).
  - Cada funcion DETECTA sus propios errores y reporta el CONJUNTO de tipos que
    tuvo (no un digito ya colapsado).
  - main REUNE los tipos de error del maestro + los del loop de teselas, colapsa
    a un unico digito (0 / unico / 9 = multiples) y hace sys.exit(digito).
  - El .bat compone el codigo final: (TPK_RC * 100) + MMPK_RC.
  - Errores interruptores (sin datos que impiden seguir) se lanzan como
    ErrorTPKException y main los atrapa y traduce a sys.exit. Un except generico
    final atrapa cualquier excepcion futura no prevista -> sys.exit(8).

Convenciones que sustentan el codigo (validadas empiricamente):
  - saveACopy() (arcpy.mp) exporta grupos con simbologia + arbol anidado y
    produce un .lyrx compatible con LayerFile. NO usar la GP tool
    SaveToLayerFile (genera un .lyrx incompatible).
  - addLayerToGroup(grupo, lyrx, "BOTTOM") preserva el orden.
  - saveACopy NO falla ante capa rota: se detecta con isBroken (proactivo).
  - arcpy.mp no crea proyectos vacios: se parte de template_base_vacio.aprx.
  - La reference scale es propiedad del MAPA (no viaja en el .lyrx): se hereda
    del primer APRX de servicio al maestro; los APRX de region la heredan al
    copiarse del maestro.
  - El TPK VRED va suelto AL FONDO del TOC (es basemap, no consultable).
"""

import arcpy
import os
import sys
import shutil
import datetime
import time
import traceback
from enum import IntEnum


# ══════════════════════════════════════════════════════════════════════════
# CATALOGO DE ERRORES (NIVEL MODULO — compartido por las funciones y main)
# ══════════════════════════════════════════════════════════════════════════

class ErrorTPK(IntEnum):
    """Fuente de verdad de los codigos de error de TPK (digito de centena).
    Semantica uniforme del digito: 0 = sin error; 1..8 = un unico tipo (el
    numero identifica el subtipo); 9 = mas de un tipo simultaneo.
    El .name da el nombre legible; el valor (int) es el codigo para el .bat.
    Estan definidos los 10 valores (0-9) para que ErrorTPK(n) sea siempre valido.
    """
    TPK_OK           = 0   # sin error de TPK
    TPK_ERR_GEN      = 1   # error de generacion de TPK (teselas no guardadas)
    TPK_CAPA_ROTA    = 2   # TPK generado con al menos una capa rota (se incluye)
    TPK_SIN_DATOS    = 3   # APRX sin mapa "Mapa" o mapa sin capas (se saltea)
    TPK_ERR_LECTURA  = 4   # no se pudo abrir/leer el .aprx de servicio
    TPK_NO_REF_SCALE = 5   # no se pudo aplicar ninguna reference scale
    TPK_RESERVADO6   = 6   # reservado (futuro)
    TPK_RESERVADO7   = 7   # reservado (futuro)
    TPK_ERR_INESPERADO = 8 # error desconocido / sin atrapar (red de seguridad)
    MULTIP_ERRORES   = 9   # mas de un tipo de error de TPK simultaneo


class ErrorTPKException(Exception):
    """Excepcion de negocio del TPK-Generator. Lleva el codigo (miembro de
    ErrorTPK) adentro para que main lo traduzca a sys.exit sin ambiguedad.
    Se usa SOLO para errores interruptores (que impiden continuar)."""
    def __init__(self, codigo, mensaje=""):
        self.codigo = codigo                      # miembro de ErrorTPK
        super().__init__(mensaje or codigo.name)


# ══════════════════════════════════════════════════════════════════════════
# CONFIGURACION
# ══════════════════════════════════════════════════════════════════════════
# [CONSERVAR / VERIFICAR CONTRA TU SCRIPT ACTUAL]
# cargar_configuracion() y las variables de config son las de tu script.
# Se reproducen aca segun los fragmentos conocidos; verificar nombres/rutas.

config = cargar_configuracion()                                   # <- tu funcion
STARTUP_PATH = config['STARTUP_PATH']
strFile      = STARTUP_PATH + config['LOG_FOLDER']
SOURCE       = STARTUP_PATH + config['SOURCE']
FEATURE_VIEW = config['FEATURE_VIEW']
SALIDA       = STARTUP_PATH + config['SALIDA']
APRX_VRED_BASEMAP = arcpy.mp.ArcGISProject(STARTUP_PATH + config['APRX_VRED_BASEMAP'])
LOD_ESRI     = config['LOD_ESRI']
LOD_VRED     = config['LOD_VRED']
OUT_TPK_POST = config['OUT_TPK_POST']
CAPAS_EXCLUIDAS_BASEMAP    = set(config.get('CAPAS_EXCLUIDAS_BASEMAP', []))
CAPAS_EXCLUIDAS_APPOFFLINE = set(config.get('CAPAS_EXCLUIDAS_APPOFFLINE', []))

# Rutas derivadas para el refactor del lyrx maestro
TEMPLATE_DIR   = STARTUP_PATH + r"\Template"
TEMP_LYRX_DIR  = TEMPLATE_DIR + r"\temp_lyrx"
PLANTILLA_BASE = TEMPLATE_DIR + r"\template_base_vacio.aprx"
APRX_MAESTRO   = SALIDA + r"\APRX_MAESTRO.aprx"
NOMBRE_MAPA    = "Mapa"
PREFIJOS_EXCLUIDOS = ("old_", "template_base_vacio")


# ══════════════════════════════════════════════════════════════════════════
# LOG
# ══════════════════════════════════════════════════════════════════════════
# [CONSERVAR / VERIFICAR CONTRA TU SCRIPT ACTUAL]
# Se reproduce una firma simple log(message); usar la de tu script (que
# probablemente ademas escribe a archivo en strFile).

def log(message):
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"{ts} {message}")
    # [CONSERVAR] si tu log escribe a archivo, mantener esa parte aca.


# ══════════════════════════════════════════════════════════════════════════
# FUNCIONES DEL REFACTOR (auxiliares)
# ══════════════════════════════════════════════════════════════════════════

def _es_primer_nivel(lyr):
    """True si la capa/grupo cuelga directo del mapa (sin '\\' en longName)."""
    return "\\" not in lyr.longName


def _descubrir_aprx_servicio():
    """Lista los .aprx de PRIMER NIVEL de Template (no recursivo) que no
    empiezan con los prefijos excluidos (old_, template_base_vacio). Orden
    alfabetico ascendente (NN_nombre garantiza el orden correcto)."""
    incluidos = []
    for nombre in sorted(os.listdir(TEMPLATE_DIR)):
        ruta = os.path.join(TEMPLATE_DIR, nombre)
        if not os.path.isfile(ruta):
            continue                       # ignora subcarpetas (temp_lyrx, etc.)
        if not nombre.lower().endswith(".aprx"):
            continue                       # ignora .atbx y otros
        if any(nombre.lower().startswith(p.lower()) for p in PREFIJOS_EXCLUIDOS):
            log(f"[DESCUBRIR] EXCLUIDO: {nombre}")     # se ignora old_/template
            continue
        log(f"[DESCUBRIR] INCLUIDO: {nombre}")
        incluidos.append(nombre)
    return incluidos


# ══════════════════════════════════════════════════════════════════════════
# construir_lyrx_maestro
# ══════════════════════════════════════════════════════════════════════════

def construir_lyrx_maestro():
    """Construye el APRX maestro con un grupo por APRX de servicio. Se llama
    UNA VEZ antes del loop de subregiones.

    Detecta sus propios errores y los reune en un SET de tipos (ErrorTPK). NO
    colapsa el digito final: eso lo hace main (que ve tambien el error de
    teselas). El finally calcula un subtipo informativo para el log y escribe
    el resumen SIEMPRE.

    Interruptores (lanzan ErrorTPKException, main los atrapa):
      - No hay APRX de servicio en Template.
      - No se pudo procesar ninguno (grupos_creados == 0 al terminar).

    No interruptores (se acumulan como tipos y se devuelven en el set):
      - Capa rota (TPK_CAPA_ROTA), sin datos por APRX (TPK_SIN_DATOS), error de
        lectura por APRX (TPK_ERR_LECTURA), sin reference scale (TPK_NO_REF_SCALE).

    Devuelve (ruta_maestro, tipos_error:set[ErrorTPK], resumen:dict).
    """
    # --- Inicializar TODO antes del try (el finally los usa siempre) --------
    aprx_servicios = []
    grupos_creados = 0
    reference_scale_aplicada = None
    hay_origen_sin_datos = False
    hubo_error_lectura = False
    hubo_capa_rota = False
    capas_rotas_final = []
    capas_excluidas_total = []
    aprx_sin_datos = []
    aprx_error_lectura = []
    tipos_error = set()
    aprx_maestro = None
    mapa_maestro = None

    try:
        log("=" * 70)
        log("CONSTRUCCION DEL LYRX MAESTRO")
        log("=" * 70)

        # --- Purga de temp_lyrx al inicio (mecanismo principal de limpieza) --
        if os.path.exists(TEMP_LYRX_DIR):
            try:
                shutil.rmtree(TEMP_LYRX_DIR)
                log(f"[PURGA] temp_lyrx eliminada: {TEMP_LYRX_DIR}")
            except Exception as e:
                log(f"[WARNING] No se pudo purgar temp_lyrx completa: {e}")
        os.makedirs(TEMP_LYRX_DIR, exist_ok=True)

        # --- Descubrir APRX de servicio ------------------------------------
        aprx_servicios = _descubrir_aprx_servicio()
        if not aprx_servicios:
            # INTERRUPTOR: sin APRX de servicio no se puede armar nada.
            hay_origen_sin_datos = True
            raise ErrorTPKException(ErrorTPK.TPK_SIN_DATOS,
                                    "No hay APRX de servicio en Template")
        log(f"[INFO] APRX de servicio a procesar ({len(aprx_servicios)}): {aprx_servicios}")

        # --- Preparar APRX base (copiar plantilla; arcpy no crea vacios) ----
        if os.path.exists(APRX_MAESTRO):
            os.remove(APRX_MAESTRO)
        shutil.copyfile(PLANTILLA_BASE, APRX_MAESTRO)
        aprx_maestro = arcpy.mp.ArcGISProject(APRX_MAESTRO)
        mapa_maestro = aprx_maestro.listMaps(NOMBRE_MAPA)[0]

        # --- Loop por APRX de servicio -------------------------------------
        for nombre_aprx in aprx_servicios:
            ruta_aprx = os.path.join(TEMPLATE_DIR, nombre_aprx)
            nombre_grupo = os.path.splitext(nombre_aprx)[0]     # ej: 03_RED_HFC

            # Error de LECTURA: el .aprx no se puede abrir (corrupto/bloqueado)
            try:
                aprx_serv = arcpy.mp.ArcGISProject(ruta_aprx)
                mapas = aprx_serv.listMaps(NOMBRE_MAPA)
            except Exception as e:
                hubo_error_lectura = True
                aprx_error_lectura.append(nombre_aprx)
                log(f"[ERROR LECTURA] No se pudo abrir {nombre_aprx}: {repr(e)}")
                continue                                        # skip, sigue

            # SIN DATOS: no tiene mapa "Mapa"
            if not mapas:
                hay_origen_sin_datos = True
                aprx_sin_datos.append(nombre_aprx)
                log(f"[SIN DATOS] {nombre_aprx} no tiene mapa '{NOMBRE_MAPA}'")
                continue                                        # skip, sigue
            mapa_serv = mapas[0]

            # SIN DATOS: el mapa no tiene capas (ej. vaciado por error)
            if not mapa_serv.listLayers():
                hay_origen_sin_datos = True
                aprx_sin_datos.append(nombre_aprx)
                log(f"[SIN DATOS] {nombre_aprx} no tiene capas")
                continue                                        # skip, sigue

            # Heredar reference scale del PRIMER APRX de servicio al maestro.
            if reference_scale_aplicada is None:
                try:
                    rs = mapa_serv.referenceScale
                    mapa_maestro.referenceScale = rs
                    reference_scale_aplicada = rs
                    log(f"[REFERENCE SCALE] Heredada del servicio: 1:{rs}")
                except Exception as e:
                    log(f"[WARNING] No se pudo heredar reference scale: {repr(e)}")

            # Carpeta temp_lyrx propia de este APRX (evita colisiones)
            carpeta_lyrx = os.path.join(TEMP_LYRX_DIR, nombre_grupo)
            os.makedirs(carpeta_lyrx, exist_ok=True)

            # Grupo contenedor NN_nombre en el maestro
            grupo_cont = mapa_maestro.createGroupLayer(nombre_grupo)
            grupos_creados += 1

            # Exportar cada elemento de primer nivel a .lyrx y reimportarlo.
            # (La exclusion y la deteccion de rotas se hacen DESPUES, sobre el
            #  maestro completo, para cubrir cualquier nivel — ver mas abajo.)
            elementos = [l for l in mapa_serv.listLayers() if _es_primer_nivel(l)]
            for elem in elementos:
                destino = os.path.join(carpeta_lyrx, f"{elem.name}.lyrx")
                try:
                    elem.saveACopy(destino)
                    if not (os.path.exists(destino) and os.path.getsize(destino) > 0):
                        log(f"[ERROR] No se genero el lyrx de {elem.name} ({nombre_aprx})")
                        continue
                    log(f"[LYRX] Generado {elem.name}.lyrx ({nombre_grupo})")
                except Exception as e:
                    log(f"[ERROR] Fallo la generacion del lyrx de {elem.name}: {repr(e)}")
                    continue
                try:
                    lf = arcpy.mp.LayerFile(destino)
                    mapa_maestro.addLayerToGroup(grupo_cont, lf, "BOTTOM")
                except Exception as e:
                    log(f"[ERROR] Fallo la importacion de {elem.name}: {repr(e)}")
                    continue

        # INTERRUPTOR: si tras el loop no se armo ningun grupo, no hay maestro.
        if grupos_creados == 0:
            hay_origen_sin_datos = True
            raise ErrorTPKException(ErrorTPK.TPK_SIN_DATOS,
                                    "No se pudo procesar ningun APRX de servicio")

        # --- Exclusion de capas en CUALQUIER nivel (sobre el maestro armado)-
        # Recorre el maestro aplanado y remueve por nombre. Se hace UNA vez;
        # todas las regiones lo heredan (mismo nivel de datos). Solo capas de
        # datos, no grupos contenedores.
        if CAPAS_EXCLUIDAS_APPOFFLINE:
            for lyr in list(mapa_maestro.listLayers()):
                if lyr.isGroupLayer:
                    continue
                if lyr.name in CAPAS_EXCLUIDAS_APPOFFLINE:
                    try:
                        mapa_maestro.removeLayer(lyr)
                        log(f"[EXCLUIDA] {lyr.longName}")
                        capas_excluidas_total.append(lyr.longName)
                    except Exception as e:
                        log(f"[ERROR] No se pudo excluir {lyr.longName}: {repr(e)}")

        # --- Guardar el maestro --------------------------------------------
        aprx_maestro.save()
        log(f"[APRX] Maestro generado: {os.path.basename(APRX_MAESTRO)}")

        # --- Deteccion de capa rota SOBRE EL MAESTRO FINAL (post-exclusion) -
        # Una capa excluida no cuenta como rota. saveACopy no falla ante rota,
        # por eso se detecta con isBroken aca, sobre lo que quedo en el maestro.
        for lyr in mapa_maestro.listLayers():
            if lyr.isGroupLayer:
                continue
            try:
                if lyr.supports("DATASOURCE") and lyr.isBroken:
                    capas_rotas_final.append(lyr.longName)
                    log(f"[CAPA ROTA] {lyr.longName}")
            except Exception:
                capas_rotas_final.append(lyr.longName + " (isBroken indeterminado)")
                log(f"[CAPA ROTA] {lyr.longName} (isBroken indeterminado)")
        hubo_capa_rota = len(capas_rotas_final) > 0

    finally:
        # ── El manejo de errores y el resumen se ejecutan SIEMPRE ──────────
        # (haya o no excepcion; el finally corre antes de que la excepcion
        #  llegue a main). NO hace return ni raise: solo calcula y loguea.

        # error_ref_scale: solo es error si se procesaron APRX pero ninguno
        # aporto ref scale (si no se proceso nada, la causa raiz es sin datos).
        error_ref_scale = (reference_scale_aplicada is None) and (grupos_creados > 0)

        # Reunir el CONJUNTO de tipos de error de esta corrida (sin colapsar).
        if hubo_capa_rota:
            tipos_error.add(ErrorTPK.TPK_CAPA_ROTA)
        if hay_origen_sin_datos:
            tipos_error.add(ErrorTPK.TPK_SIN_DATOS)
        if hubo_error_lectura:
            tipos_error.add(ErrorTPK.TPK_ERR_LECTURA)
        if error_ref_scale:
            tipos_error.add(ErrorTPK.TPK_NO_REF_SCALE)

        # Subtipo informativo para el log (main hace el colapso final real).
        if not tipos_error:
            subtipo_info = ErrorTPK.TPK_OK
        elif len(tipos_error) == 1:
            subtipo_info = next(iter(tipos_error))
        else:
            subtipo_info = ErrorTPK.MULTIP_ERRORES

        log("=" * 70)
        log("RESUMEN CONSTRUCCION LYRX MAESTRO")
        log(f"  APRX a procesar     : {len(aprx_servicios)}")
        log(f"  Grupos creados      : {grupos_creados}")
        log(f"  APRX error lectura  : {len(aprx_error_lectura)} {aprx_error_lectura or ''}")
        log(f"  APRX sin datos      : {len(aprx_sin_datos)} {aprx_sin_datos or ''}")
        log(f"  Capas excluidas     : {len(capas_excluidas_total)}")
        log(f"  Capas ROTAS (maestro): {len(capas_rotas_final)}")
        for capa in capas_rotas_final:
            log(f"      - {capa}")
        log(f"  Reference scale     : {reference_scale_aplicada}")
        log(f"  Tipos de error TPK  : {[t.name for t in tipos_error] or 'ninguno'}")
        log(f"  Subtipo (informativo): {int(subtipo_info)} - {subtipo_info.name}")
        log("=" * 70)

    # ── return en el flujo normal (fuera del try/finally). Si hubo un
    #    ErrorTPKException interruptor, NO se llega aca: propago a main. ──────
    resumen = {
        "aprx_procesados": len(aprx_servicios),
        "grupos_creados": grupos_creados,
        "aprx_error_lectura": aprx_error_lectura,
        "aprx_sin_datos": aprx_sin_datos,
        "capas_rotas": capas_rotas_final,
        "capas_excluidas": capas_excluidas_total,
        "reference_scale": reference_scale_aplicada,
        "tipos_error": tipos_error,
    }
    return APRX_MAESTRO, tipos_error, resumen


# ══════════════════════════════════════════════════════════════════════════
# preparar_aprx_region
# ══════════════════════════════════════════════════════════════════════════

def preparar_aprx_region(region, tpk_path_VRED_BASEMAP):
    """Copia el maestro como APRX_{region}, agrega el TPK VRED como capa suelta
    al fondo (basemap, no consultable) y guarda. La reference scale se hereda
    al copiar el maestro. Devuelve la ruta del APRX de region."""
    ruta_region = os.path.join(SALIDA, f"APRX_{region}.aprx")
    if os.path.exists(ruta_region):
        os.remove(ruta_region)
    shutil.copyfile(APRX_MAESTRO, ruta_region)

    aprx_region = arcpy.mp.ArcGISProject(ruta_region)
    mapa_region = aprx_region.listMaps(NOMBRE_MAPA)[0]

    capas_antes = list(mapa_region.listLayers())
    mapa_region.addDataFromPath(os.path.abspath(tpk_path_VRED_BASEMAP))

    nuevas = [l for l in mapa_region.listLayers() if l not in capas_antes]
    if not nuevas:
        log(f"[WARNING] No se detecto la capa del TPK VRED agregada en {region}")
    else:
        tpk_layer = nuevas[0]
        nivel_raiz = [l for l in mapa_region.listLayers() if _es_primer_nivel(l)]
        otros_raiz = [l for l in nivel_raiz if l is not tpk_layer]
        if otros_raiz:
            try:
                mapa_region.moveLayer(otros_raiz[-1], tpk_layer, "AFTER")
                log(f"[TPK VRED] Movido al fondo del TOC en {region}")
            except Exception as e:
                log(f"[WARNING] No se pudo mover el TPK VRED al fondo en {region}: {repr(e)}")

    aprx_region.save()
    log(f"[APRX] Generado APRX_{region}.aprx")
    return ruta_region


# ══════════════════════════════════════════════════════════════════════════
# MAIN — orquesta, compone el codigo de salida y hace sys.exit
# ══════════════════════════════════════════════════════════════════════════

def _colapsar_codigo(tipos_error):
    """Colapsa un conjunto de tipos de error de TPK a un unico digito 0-9.
    0 = sin error; el unico tipo si hay uno; 9 = mas de un tipo."""
    if not tipos_error:
        return ErrorTPK.TPK_OK
    if len(tipos_error) == 1:
        return next(iter(tipos_error))
    return ErrorTPK.MULTIP_ERRORES


def main():
    try:
        arcpy.env.overwriteOutput = True

        # ── Conjunto de TODOS los tipos de error de TPK de la corrida ───────
        # Se llena con los del maestro + el de teselas del loop. main colapsa
        # al final (unica fuente del digito de salida).
        tipos_error_tpk = set()

        # ── 1) Construir el lyrx maestro (una vez, antes del loop) ──────────
        # Puede lanzar ErrorTPKException (interruptor) -> lo atrapa el except.
        ruta_maestro, tipos_maestro, resumen_maestro = construir_lyrx_maestro()
        tipos_error_tpk |= tipos_maestro

        # ── 2) Loop de subregiones ─────────────────────────────────────────
        fecha_actual = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # [CONSERVAR / VERIFICAR CONTRA TU SCRIPT ACTUAL]
        # La obtencion de regiones_a_procesar es la de tu script (DISTINCT de
        # SUBREGION sobre la vista, filtro ACTUALIZA=1). Se reproduce el patron.
        feature_class_view = FEATURE_VIEW
        regiones_a_procesar = obtener_regiones_a_procesar()          # <- tu logica
        log(f"REGIONES a procesar: {', '.join(regiones_a_procesar)}")

        for region in regiones_a_procesar:
            try:
                region = region.strip()
                log('=' * 80)
                log(f"Iniciando generacion de TPK para: {region}")

                # ── Layer de region (areas de hub de la subregion) ──────────
                # [CONSERVAR] .lyr -> .lyrx (formato moderno)
                where_clause = f"SUBREGION = '{region}'"
                region_lyr = arcpy.management.MakeFeatureLayer(
                    feature_class_view, f"lyr_{region}", where_clause)
                arcpy.management.SaveToLayerFile(
                    region_lyr, SALIDA + "\\" + f"{region}.lyrx", 'RELATIVE')

                count = int(arcpy.management.GetCount(region_lyr).getOutput(0))
                if count == 0:
                    log(f"[!] No hay datos para {region} con ACTUALIZA=1")
                    continue

                # ── Generacion del TPK VRED de la subregion ─────────────────
                # [CONSERVAR / VERIFICAR CONTRA TU SCRIPT ACTUAL]
                # Este bloque es tu generacion actual del TPK VRED (mapCanvas,
                # exclusion CAPAS_EXCLUIDAS_BASEMAP, CreateMapTilePackage con
                # area_of_interest=region_lyr, LOD_VRED, etc.). Se reproduce el
                # esqueleto; completar con tu codigo real.
                tpk_path_VRED_BASEMAP = os.path.join(
                    SALIDA, f'TPK_{region}_{OUT_TPK_POST}.tpk')
                if arcpy.Exists(tpk_path_VRED_BASEMAP):
                    arcpy.management.Delete(tpk_path_VRED_BASEMAP)

                try:
                    # <<< TU BLOQUE ACTUAL DE GENERACION DEL TPK VRED >>>
                    generar_tpk_vred(region, region_lyr, tpk_path_VRED_BASEMAP)  # <- tu logica
                    if not arcpy.Exists(tpk_path_VRED_BASEMAP):
                        raise RuntimeError("TPK VRED no se genero")
                    log(f"[TPK VRED creado exitosamente] para {region}")
                except Exception as e:
                    # Error de GENERACION de TPK (teselas): tipo 1. Se acumula y
                    # se saltea la region (sin TPK no hay APRX de region util).
                    tipos_error_tpk.add(ErrorTPK.TPK_ERR_GEN)
                    log(f"[ERROR] No se pudo generar el TPK VRED de {region}: {repr(e)}")
                    continue

                log('-' * 40)

                # ── Armar el APRX de region (maestro + TPK VRED al fondo) ────
                # Reemplaza el armado anterior desde APRX_APP_OFFLINE.aprx.
                # La exclusion de capas YA se hizo en el maestro (no va aca).
                preparar_aprx_region(region, tpk_path_VRED_BASEMAP)

            except Exception as e:
                # Un error inesperado en una region no debe tumbar el resto.
                # Se registra como error de generacion de TPK y se continua.
                tipos_error_tpk.add(ErrorTPK.TPK_ERR_GEN)
                log(f"[ERROR] Fallo inesperado en la region {region}: {repr(e)}")
                log(traceback.format_exc())
                continue

        # ── 3) Componer el codigo de salida y salir ────────────────────────
        codigo = _colapsar_codigo(tipos_error_tpk)
        log("#" * 70)
        log(f"[CODIGO DE SALIDA TPK] {int(codigo)} - {codigo.name}")
        if len(tipos_error_tpk) > 1:
            log(f"[DETALLE] Errores multiples: {[t.name for t in tipos_error_tpk]}")
        log("#" * 70)
        sys.exit(int(codigo))

    except ErrorTPKException as e:
        # Error de negocio interruptor (ej. sin APRX de servicio). El resumen
        # ya se escribio en el finally de la funcion que lo lanzo.
        log(f"[ERROR TPK] {e.codigo.name}: {e}")
        sys.exit(int(e.codigo))

    except SystemExit:
        # Dejar pasar el sys.exit intencional (no atraparlo como error).
        raise

    except Exception:
        # Red de seguridad: cualquier excepcion futura no prevista (o que un
        # proximo desarrollador agregue y no atrape). Nunca el exit 1 generico.
        log("[ERROR DESCONOCIDO / SIN ATRAPAR]")
        log(traceback.format_exc())
        sys.exit(int(ErrorTPK.TPK_ERR_INESPERADO))   # 8


if __name__ == "__main__":
    main()
