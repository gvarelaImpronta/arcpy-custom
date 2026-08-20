# -*- coding: utf-8 -*-
# ══════════════════════════════════════════════════════════════════════════
# REFACTOR TPK-Generator — Armado dinamico del TPK via lyrx maestro
# ══════════════════════════════════════════════════════════════════════════
#
# Este archivo consolida TODO lo que hay que llevar al TPK-Generator:
#
#   PARTE 1 — FUNCIONES NUEVAS (ya validadas end-to-end en pruebas):
#             construir_lyrx_maestro() y preparar_aprx_region(), mas sus
#             auxiliares y las constantes de subtipo de error de TPK.
#
#   PARTE 2 — CAMBIOS A APLICAR sobre el TPK-Generator existente (guia de
#             integracion: que agregar, que reemplazar, donde). El codigo del
#             loop de subregiones se codea en el paso siguiente, cuando este
#             archivo este integrado.
#
# Convenciones ya acordadas que sustentan el codigo (no repetir en cada func):
#   - saveACopy() (metodo arcpy.mp) exporta grupos con simbologia + arbol
#     anidado y produce un .lyrx compatible con LayerFile. NO usar la GP tool
#     SaveToLayerFile (genera un .lyrx incompatible).
#   - addLayerToGroup(grupo, lyrx, "BOTTOM") preserva el orden.
#   - saveACopy NO falla ante capa rota: se detecta con isBroken (proactivo).
#   - arcpy.mp no crea proyectos vacios: se parte de template_base_vacio.aprx.
#   - La reference scale es propiedad del MAPA (no viaja en el .lyrx): se hereda
#     del primer APRX de servicio al maestro; los APRX de region la heredan al
#     copiarse del maestro.
#   - El TPK VRED va suelto AL FONDO del TOC (es basemap, no consultable); ya
#     NO se busca la capa TRANSFORMADOR ni se hace moveLayer relativo a ella.
# ══════════════════════════════════════════════════════════════════════════

import arcpy
import os
import shutil


# ══════════════════════════════════════════════════════════════════════════
# PARTE 1 — FUNCIONES NUEVAS (validadas)
# ══════════════════════════════════════════════════════════════════════════

# --- Subtipos de error de TPK (digito de centena del codigo de salida) ------
# Semantica uniforme por digito del codigo de salida de 3 digitos:
#   0 = sin error de esa categoria
#   1..8 = un unico tipo de error (el numero identifica el subtipo)
#   9 = mas de un tipo de error simultaneo en esa categoria
# Centena = TPK, decena = generacion MMPK, unidad = subida Portal.
TPK_OK          = 0   # sin problemas
TPK_ERR_GEN     = 1   # error de generacion de TPK (no se guardo el TPK)
TPK_CAPA_ROTA   = 2   # TPK generado con al menos una capa rota (se incluye igual)
# 3..8 reservados para futuros tipos de error de TPK
# 9 = multiples tipos de error de TPK simultaneos (lo compone el llamador)


def _es_primer_nivel(lyr):
    """True si la capa/grupo cuelga directo del mapa (primer nivel).
    En arcpy.mp, longName usa '\\' como separador de jerarquia; los de
    primer nivel no tienen separador."""
    return "\\" not in lyr.longName


def _capas_rotas_bajo(elemento, mapa):
    """Devuelve la lista de longName de las capas rotas que cuelgan del
    elemento de primer nivel dado (recorre toda su rama, cualquier nivel).
    saveACopy no falla ante capa rota, por eso se detecta con isBroken.
    Contempla el caso de capa suelta (el elemento es la propia capa)."""
    rotas = []
    prefijo = elemento.name + "\\"
    for capa in mapa.listLayers():
        if capa.isGroupLayer:
            continue
        if capa.longName == elemento.name or capa.longName.startswith(prefijo):
            try:
                if capa.supports("DATASOURCE") and capa.isBroken:
                    rotas.append(capa.longName)
            except Exception:
                rotas.append(capa.longName + " (isBroken indeterminado)")
    return rotas


def _descubrir_aprx_servicio(template_dir, prefijos_excluidos, log):
    """Lista los .aprx de PRIMER NIVEL de Template (no recursivo) que no
    empiezan con ninguno de los prefijos excluidos. Orden alfabetico
    ascendente (el formato NN_nombre garantiza el orden correcto)."""
    todos = []
    for nombre in sorted(os.listdir(template_dir)):
        ruta = os.path.join(template_dir, nombre)
        if not os.path.isfile(ruta):
            continue  # ignora subcarpetas (temp_lyrx u otras)
        if not nombre.lower().endswith(".aprx"):
            continue  # ignora .atbx y otros
        base = nombre.lower()
        if any(base.startswith(p.lower()) for p in prefijos_excluidos):
            log(f"[DESCUBRIR] EXCLUIDO: {nombre}")
            continue
        log(f"[DESCUBRIR] INCLUIDO: {nombre}")
        todos.append(nombre)
    return todos


def construir_lyrx_maestro(
    template_dir,
    salida_dir,
    plantilla_base,
    capas_excluidas,          # set de nombres de capa a excluir (CAPAS_EXCLUIDAS_APPOFFLINE)
    nombre_mapa,              # "Mapa"
    aprx_maestro_salida,      # ruta del .aprx maestro a generar
    log,                      # funcion de logging del TPK-Generator
    prefijos_excluidos=("old_", "template_base_vacio"),
):
    """Construye el APRX maestro con un grupo por APRX de servicio.

    Se llama UNA VEZ antes del loop de subregiones. Purga temp_lyrx y el
    maestro previo, descubre los APRX de servicio, y por cada uno exporta sus
    elementos de primer nivel a .lyrx y los reimporta en un grupo homonimo,
    preservando anidamiento/simbologia/orden. Hereda la reference scale del
    primer APRX de servicio. Detecta capas rotas proactivamente.

    Devuelve (ruta_aprx_maestro, subtipo_error_tpk, resumen_dict):
      subtipo_error_tpk: 0 (ok) o 2 (hubo capa rota).
    """
    log("=" * 70)
    log("CONSTRUCCION DEL LYRX MAESTRO")
    log("=" * 70)

    temp_lyrx = os.path.join(template_dir, "temp_lyrx")

    # --- Purga de temp_lyrx al inicio (mecanismo principal de limpieza) ------
    if os.path.exists(temp_lyrx):
        try:
            shutil.rmtree(temp_lyrx)
            log(f"[PURGA] temp_lyrx eliminada: {temp_lyrx}")
        except Exception as e:
            log(f"[WARNING] No se pudo purgar temp_lyrx completa: {e}")
    os.makedirs(temp_lyrx, exist_ok=True)

    # --- Descubrir APRX de servicio -----------------------------------------
    aprx_servicios = _descubrir_aprx_servicio(template_dir, prefijos_excluidos, log)
    if not aprx_servicios:
        log("[ERROR] No se encontro ningun APRX de servicio en Template")
        raise RuntimeError("No hay APRX de servicio para construir el maestro.")
    log(f"[INFO] APRX de servicio a procesar ({len(aprx_servicios)}): {aprx_servicios}")

    # --- Preparar APRX base (copiar plantilla; arcpy.mp no crea vacios) ------
    if os.path.exists(aprx_maestro_salida):
        os.remove(aprx_maestro_salida)
    shutil.copyfile(plantilla_base, aprx_maestro_salida)
    log(f"[OK] Plantilla base copiada a maestro: {aprx_maestro_salida}")

    aprx_maestro = arcpy.mp.ArcGISProject(aprx_maestro_salida)
    mapa_maestro = aprx_maestro.listMaps(nombre_mapa)[0]

    # --- Acumuladores de estado ---------------------------------------------
    hubo_capa_rota = False
    aprx_con_error_apertura = []
    capas_rotas_total = []
    capas_excluidas_total = []
    grupos_creados = 0
    reference_scale_aplicada = None   # se hereda del primer APRX de servicio

    # --- Loop por APRX de servicio (try/except por APRX) --------------------
    for nombre_aprx in aprx_servicios:
        ruta_aprx = os.path.join(template_dir, nombre_aprx)
        nombre_sin_ext = os.path.splitext(nombre_aprx)[0]   # ej: 03_RED_HFC
        log("-" * 70)
        log(f"[APRX] Procesando: {nombre_aprx}  (grupo destino: {nombre_sin_ext})")

        try:
            aprx_serv = arcpy.mp.ArcGISProject(ruta_aprx)
            mapas = aprx_serv.listMaps(nombre_mapa)
            if not mapas:
                raise RuntimeError(f"El APRX no tiene un mapa llamado '{nombre_mapa}'")
            mapa_serv = mapas[0]
        except Exception as e:
            log(f"[ERROR APRX] No se pudo abrir/leer {nombre_aprx}: {repr(e)}")
            aprx_con_error_apertura.append(nombre_aprx)
            continue

        # Heredar reference scale del primer APRX de servicio al maestro.
        # Es propiedad del MAPA (no viaja en el .lyrx); sin ella los simbolos
        # dependientes de escala no escalan en el maestro.
        if reference_scale_aplicada is None:
            try:
                rs = mapa_serv.referenceScale
                mapa_maestro.referenceScale = rs
                reference_scale_aplicada = rs
                log(f"[REFERENCE SCALE] Heredada del servicio: 1:{rs}")
            except Exception as e:
                log(f"[WARNING] No se pudo heredar reference scale: {repr(e)}")

        # Carpeta temp_lyrx propia de este APRX (evita colisiones de nombres)
        carpeta_lyrx = os.path.join(temp_lyrx, nombre_sin_ext)
        os.makedirs(carpeta_lyrx, exist_ok=True)

        # Elementos de primer nivel (grupos + capas sueltas), en orden
        elementos = [l for l in mapa_serv.listLayers() if _es_primer_nivel(l)]
        log(f"[APRX] Elementos de primer nivel: {[e.name for e in elementos]}")

        # Crear el grupo contenedor NN_nombre en el maestro
        try:
            grupo_cont = mapa_maestro.createGroupLayer(nombre_sin_ext)
            grupos_creados += 1
            log(f"[GRUPO] Contenedor creado: {nombre_sin_ext}")
        except Exception as e:
            log(f"[ERROR GRUPO] No se pudo crear el grupo {nombre_sin_ext}: {repr(e)}")
            aprx_con_error_apertura.append(nombre_aprx)
            continue

        # Procesar cada elemento de primer nivel
        # (La exclusion de capas NO se hace aca: se aplica sobre el maestro ya
        #  armado, recorriendo todos los niveles, para poder excluir tambien
        #  capas anidadas dentro de grupos. Ver bloque post-armado mas abajo.)
        for elem in elementos:
            # --- Deteccion proactiva de capas rotas (SE LOGEA) ---
            rotas = _capas_rotas_bajo(elem, mapa_serv)
            if rotas:
                hubo_capa_rota = True
                for r in rotas:
                    log(f"[CAPA ROTA] {nombre_aprx} :: {r}")
                    capas_rotas_total.append((nombre_aprx, r))
                # Se incluye igual (decision de diseno): no se saltea.

            # --- Exportar el elemento a .lyrx (grupo arrastra su arbol) ---
            destino = os.path.join(carpeta_lyrx, f"{elem.name}.lyrx")
            try:
                elem.saveACopy(destino)
                if not (os.path.exists(destino) and os.path.getsize(destino) > 0):
                    log(f"[ERROR] No se genero el lyrx de {elem.name} ({nombre_aprx})")
                    continue
                log(f"[LYRX] Generado {elem.name}.lyrx ({nombre_sin_ext})")   # SE LOGEA
            except Exception as e:
                log(f"[ERROR] Fallo la generacion del lyrx de {elem.name}: {repr(e)}")
                continue

            # --- Reimportar el .lyrx dentro del grupo contenedor (BOTTOM) ---
            try:
                lf = arcpy.mp.LayerFile(destino)
                mapa_maestro.addLayerToGroup(grupo_cont, lf, "BOTTOM")
            except Exception as e:
                log(f"[ERROR] Fallo la importacion de {elem.name} al maestro: {repr(e)}")
                continue

    # --- Exclusion de capas en CUALQUIER nivel (sobre el maestro armado) -----
    # Se recorre el maestro completo (listLayers aplana todos los niveles) y se
    # remueven las capas cuyo nombre esta en capas_excluidas. Se hace UNA vez
    # sobre el maestro; todas las regiones lo heredan al copiarse (mismo nivel
    # de datos en todas). Cubre capas sueltas y capas anidadas dentro de grupos.
    if capas_excluidas:
        for lyr in list(mapa_maestro.listLayers()):
            if lyr.isGroupLayer:
                continue  # solo se excluyen capas de datos, no grupos contenedores
            if lyr.name in capas_excluidas:
                try:
                    mapa_maestro.removeLayer(lyr)
                    log(f"[EXCLUIDA] {lyr.longName}")
                    capas_excluidas_total.append(lyr.longName)
                except Exception as e:
                    log(f"[ERROR] No se pudo excluir {lyr.longName}: {repr(e)}")

    # --- Guardar el maestro --------------------------------------------------
    aprx_maestro.save()
    log(f"[APRX] Maestro generado: {os.path.basename(aprx_maestro_salida)}")   # SE LOGEA

    # Recalcular el estado de capa rota SOBRE EL MAESTRO FINAL (post-exclusion):
    # una capa excluida no debe contar como rota. Se reevalua isBroken sobre las
    # capas que efectivamente quedaron en el maestro.
    capas_rotas_final = []
    for lyr in mapa_maestro.listLayers():
        if lyr.isGroupLayer:
            continue
        try:
            if lyr.supports("DATASOURCE") and lyr.isBroken:
                capas_rotas_final.append(lyr.longName)
        except Exception:
            capas_rotas_final.append(lyr.longName + " (isBroken indeterminado)")
    hubo_capa_rota = len(capas_rotas_final) > 0
    subtipo = TPK_CAPA_ROTA if hubo_capa_rota else TPK_OK

    # --- Resumen final (SE LOGEA) -------------------------------------------
    log("=" * 70)
    log("RESUMEN CONSTRUCCION LYRX MAESTRO")
    log(f"  APRX procesados        : {len(aprx_servicios)}")
    log(f"  Grupos creados         : {grupos_creados}")
    log(f"  APRX con error apertura: {len(aprx_con_error_apertura)} {aprx_con_error_apertura or ''}")
    log(f"  Capas excluidas        : {len(capas_excluidas_total)}")
    log(f"  Capas ROTAS (en maestro): {len(capas_rotas_final)}")
    for capa in capas_rotas_final:
        log(f"      - {capa}")
    log(f"  Reference scale        : 1:{reference_scale_aplicada}")
    log(f"  Subtipo error TPK      : {subtipo} ({'OK' if subtipo == 0 else 'CAPA ROTA'})")
    log("=" * 70)

    resumen = {
        "aprx_procesados": len(aprx_servicios),
        "grupos_creados": grupos_creados,
        "aprx_error_apertura": aprx_con_error_apertura,
        "capas_rotas": capas_rotas_final,
        "capas_excluidas": capas_excluidas_total,
        "hubo_capa_rota": hubo_capa_rota,
        "reference_scale": reference_scale_aplicada,
    }
    return aprx_maestro_salida, subtipo, resumen


def preparar_aprx_region(
    aprx_maestro,             # ruta del APRX maestro ya construido
    salida_dir,               # carpeta de salida (SALIDA)
    region,                   # nombre de la subregion
    tpk_path_VRED_BASEMAP,    # ruta al .tpk VRED de la subregion
    nombre_mapa,              # "Mapa"
    log,                      # funcion de logging
):
    """Arma el APRX de una subregion partiendo del maestro: copia el maestro
    como APRX_{region}, agrega el TPK VRED como capa suelta al fondo (es
    basemap, no consultable) y guarda. Devuelve la ruta del APRX de region.

    Se llama DENTRO del loop de subregiones, en reemplazo del bloque que hoy
    abre APRX_APP_OFFLINE.aprx. La reference scale se hereda al copiar el
    maestro. Ya no se busca TRANSFORMADOR ni se hace moveLayer relativo a ella.
    """
    ruta_region = os.path.join(salida_dir, f"APRX_{region}.aprx")
    if os.path.exists(ruta_region):
        os.remove(ruta_region)
    shutil.copyfile(aprx_maestro, ruta_region)

    aprx_region = arcpy.mp.ArcGISProject(ruta_region)
    mapa_region = aprx_region.listMaps(nombre_mapa)[0]

    # Agregar el TPK VRED (basemap). addDataFromPath puede ubicarlo arriba;
    # se identifica la capa nueva y se mueve al fondo del TOC.
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
        else:
            log(f"[TPK VRED] Unico elemento; queda como esta en {region}")

    aprx_region.save()
    log(f"[APRX] Generado APRX_{region}.aprx")   # SE LOGEA
    return ruta_region


# ══════════════════════════════════════════════════════════════════════════
# PARTE 2 — CAMBIOS A APLICAR EN EL TPK-Generator EXISTENTE
# ══════════════════════════════════════════════════════════════════════════
#
# Guia de integracion. El codigo del loop se codea en el paso siguiente; aca
# quedan enumerados los puntos de cambio para tener el mapa completo.
#
# ── 2.1  IMPORTS ───────────────────────────────────────────────────────────
#   Asegurar 'import shutil' (ademas de arcpy, os, sys, time, datetime) si no
#   estuviera ya. sys se necesita para el sys.exit() del codigo de salida.
#
# ── 2.2  CONSTANTES / RUTAS (bloque de configuracion) ──────────────────────
#   Agregar, derivadas de STARTUP_PATH/SALIDA ya existentes:
#       TEMPLATE_DIR   = STARTUP_PATH + r"\Template"
#       PLANTILLA_BASE = TEMPLATE_DIR + r"\template_base_vacio.aprx"
#       APRX_MAESTRO   = SALIDA + r"\APRX_MAESTRO.aprx"
#       NOMBRE_MAPA    = "Mapa"
#   (temp_lyrx se resuelve dentro de construir_lyrx_maestro; no hace falta aca.)
#
# ── 2.3  YA NO SE USAN ─────────────────────────────────────────────────────
#   - La apertura del APRX unico APRX_APP_OFFLINE.aprx como plantilla de datos.
#   - Cualquier referencia a la capa TRANSFORMADOR para posicionar el TPK VRED
#     (busqueda de cables_layer/TRANSFORMADOR y su moveLayer): se elimina.
#   - El .lyr del recorte de region pasa a .lyrx (formato moderno); revisar que
#     no haya rutas hardcodeadas con extension .lyr aguas abajo.
#
# ── 2.4  ANTES DEL LOOP DE SUBREGIONES (una sola vez) ──────────────────────
#   Insertar la construccion del maestro y capturar el subtipo de error de TPK:
#
#       ruta_maestro, subtipo_maestro, resumen_maestro = construir_lyrx_maestro(
#           template_dir=TEMPLATE_DIR,
#           salida_dir=SALIDA,
#           plantilla_base=PLANTILLA_BASE,
#           capas_excluidas=CAPAS_EXCLUIDAS_APPOFFLINE,
#           nombre_mapa=NOMBRE_MAPA,
#           aprx_maestro_salida=APRX_MAESTRO,
#           log=log,
#       )
#
#   'subtipo_maestro' vale 0 (ok) o 2 (hubo capa rota). Alimenta el acumulador
#   de tipos de error de TPK para la centena del codigo de salida (ver 2.6).
#
# ── 2.5  DENTRO DEL LOOP DE SUBREGIONES ────────────────────────────────────
#   Reemplazar el bloque que hoy abre APRX_APP_OFFLINE.aprx y arma el APRX de
#   region por la llamada a preparar_aprx_region, DESPUES de generar el TPK
#   VRED de la subregion (que ya existe en el flujo actual):
#
#       ruta_aprx_region = preparar_aprx_region(
#           aprx_maestro=ruta_maestro,
#           salida_dir=SALIDA,
#           region=region,
#           tpk_path_VRED_BASEMAP=tpk_path_VRED_BASEMAP,
#           nombre_mapa=NOMBRE_MAPA,
#           log=log,
#       )
#
#   Notas:
#   - El TPK VRED (tpk_path_VRED_BASEMAP) se sigue generando como hoy, con su
#     recorte por area_of_interest = region_lyr (areas de hub de la subregion).
#   - preparar_aprx_region reemplaza el armado del APRX; el resto del loop
#     (generacion del TPK VRED, guardado) se conserva.
#   - Si la generacion del TPK VRED de una subregion falla (no se guarda el
#     .tpk), marcar el tipo de error TPK_ERR_GEN (1) en el acumulador (ver 2.6).
#   - EXCLUSION DE CAPAS: NO va en el loop. La exclusion de capas
#     (CAPAS_EXCLUIDAS_APPOFFLINE) se hace UNA sola vez dentro de
#     construir_lyrx_maestro, recorriendo el maestro en TODOS los niveles y
#     removiendo las capas por nombre antes de guardar el maestro. Como cada
#     APRX_{region} se copia del maestro, todas las regiones heredan el mismo
#     nivel de datos. => ELIMINAR del loop cualquier bloque que recorra
#     map_offline.listLayers() y haga removeLayer por CAPAS_EXCLUIDAS_APPOFFLINE
#     (no tendria efecto: opera sobre otro mapa y despues del save interno).
#
# ── 2.6  CODIGO DE SALIDA (al final, tras el loop) ─────────────────────────
#   Semantica uniforme por digito (0 / 1..8 / 9). La centena (TPK) se compone
#   a partir del CONJUNTO de tipos de error de TPK ocurridos:
#
#       tipos_tpk = set()
#       if subtipo_maestro == TPK_CAPA_ROTA:
#           tipos_tpk.add(TPK_CAPA_ROTA)          # 2
#       if hubo_error_generacion_tpk:             # algun TPK VRED no se guardo
#           tipos_tpk.add(TPK_ERR_GEN)            # 1
#
#       if len(tipos_tpk) == 0:
#           centena = 0
#       elif len(tipos_tpk) == 1:
#           centena = next(iter(tipos_tpk))       # 1 o 2
#       else:
#           centena = 9                           # mas de un tipo simultaneo
#
#   El .bat compone el codigo final: (centena * 100) + MMPK_RC, donde
#   MMPK-Generator aporta decena (gen MMPK) y unidad (subida Portal), cada
#   una con la misma semantica 0/1..8/9.
#
#   TPK-Generator termina con: sys.exit(centena)
#   (el .bat captura %ERRORLEVEL% tras el python y lo combina con el de MMPK.)
#
#   El detalle de cantidad/nombres de capas rotas y de que TPK fallaron ya
#   quedo en el log (resumen del maestro + logs de generacion de TPK); el
#   codigo numerico solo alerta la categoria al operador de Control-M.
#
# ══════════════════════════════════════════════════════════════════════════
# FIN — refactor_functions.py
# ══════════════════════════════════════════════════════════════════════════
