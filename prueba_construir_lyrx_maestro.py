# -*- coding: utf-8 -*-
"""
Construccion del lyrx maestro de capas de servicio para TPK-Generator.

Reemplaza el uso del APRX unico (APRX_APP_OFFLINE.aprx) por la lectura dinamica
de los APRX de servicio de la carpeta Template. Cada APRX se convierte en un
grupo homonimo (NN_nombre) dentro de un APRX base, preservando el anidamiento
de subgrupos, la simbologia y el orden. El resultado se materializa como un
APRX maestro reutilizable por todas las subregiones.

Hallazgos validados que sustentan este codigo:
  - layer.saveACopy() exporta grupos con simbologia y arbol anidado completo,
    y produce un .lyrx compatible con arcpy.mp.LayerFile (NO usar la GP tool
    SaveToLayerFile, que genera un .lyrx incompatible).
  - addLayerToGroup(grupo, lyrx, "BOTTOM") preserva el orden de los grupos.
  - saveACopy NO falla ante una capa rota: la exporta silenciosamente. Por eso
    las capas rotas se detectan proactivamente con layer.isBroken, no con
    try/except.
  - arcpy.mp no crea proyectos vacios en memoria: se parte de una plantilla
    fisica (template_base_vacio.aprx) con un mapa llamado "Mapa".

Este modulo expone construir_lyrx_maestro(...) que devuelve:
  - ruta del APRX maestro construido (para reutilizar por subregion), y
  - el subtipo de error de TPK aportado por esta fase (para el codigo de salida):
      0 = sin problemas
      2 = hubo al menos una capa rota (TPK se genera igual)
    (el subtipo 1 = error de generacion de TPK lo maneja el flujo de teselas,
     fuera de este modulo)
"""

import arcpy
import os
import shutil


# ======================================================================
# Subtipos de error de TPK (para el digito de centena del codigo de salida)
# ======================================================================
TPK_OK          = 0   # sin problemas
TPK_ERR_GEN     = 1   # error de generacion de TPK (lo maneja el flujo de teselas)
TPK_CAPA_ROTA   = 2   # TPK generado con al menos una capa rota
# 3..8 reservados; 9 = multiples tipos (lo compone el llamador)


def _es_primer_nivel(lyr):
    """True si la capa/grupo cuelga directo del mapa (primer nivel).
    En arcpy.mp, longName usa '\\' como separador de jerarquia; los de
    primer nivel no tienen separador."""
    return "\\" not in lyr.longName


def _capas_rotas_bajo(elemento, mapa):
    """Devuelve la lista de longName de las capas rotas que cuelgan del
    elemento de primer nivel dado (recorre toda su rama, cualquier nivel).
    saveACopy no falla ante capa rota, por eso se detecta con isBroken."""
    rotas = []
    prefijo = elemento.name + "\\"
    for capa in mapa.listLayers():
        if capa.isGroupLayer:
            continue
        # pertenece a la rama del elemento (o es el elemento si fuera capa suelta)
        if capa.longName == elemento.name or capa.longName.startswith(prefijo):
            try:
                if capa.supports("DATASOURCE") and capa.isBroken:
                    rotas.append(capa.longName)
            except Exception:
                # si no se puede evaluar isBroken, se registra como sospechosa
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
        excluido = any(base.startswith(p.lower()) for p in prefijos_excluidos)
        if excluido:
            log(f"[DESCUBRIR] EXCLUIDO: {nombre}")
            continue
        log(f"[DESCUBRIR] INCLUIDO: {nombre}")
        todos.append(nombre)
    return todos


def construir_lyrx_maestro(
    template_dir,
    salida_dir,
    plantilla_base,
    capas_excluidas,          # set de nombres de capa a excluir
    nombre_mapa,              # "Mapa"
    aprx_maestro_salida,      # ruta del .aprx maestro a generar
    log,                      # funcion de logging
    prefijos_excluidos=("old_", "template_base_vacio"),
):
    """Construye el APRX maestro con un grupo por APRX de servicio.

    Devuelve (ruta_aprx_maestro, subtipo_error_tpk, resumen_dict).
      subtipo_error_tpk: 0 (ok) o 2 (hubo capa rota).
    """
    log("=" * 70)
    log("CONSTRUCCION DEL LYRX MAESTRO")
    log("=" * 70)

    temp_lyrx = os.path.join(template_dir, "temp_lyrx")

    # ------------------------------------------------------------------
    # Purga de temp_lyrx al inicio (mecanismo principal de limpieza).
    # La estructura por-carpeta permite purga selectiva/manual si esto falla.
    # ------------------------------------------------------------------
    if os.path.exists(temp_lyrx):
        try:
            shutil.rmtree(temp_lyrx)
            log(f"[PURGA] temp_lyrx eliminada: {temp_lyrx}")
        except Exception as e:
            log(f"[WARNING] No se pudo purgar temp_lyrx completa: {e}")
    os.makedirs(temp_lyrx, exist_ok=True)

    # ------------------------------------------------------------------
    # Descubrir APRX de servicio a procesar
    # ------------------------------------------------------------------
    aprx_servicios = _descubrir_aprx_servicio(template_dir, prefijos_excluidos, log)
    if not aprx_servicios:
        raise RuntimeError("No se encontro ningun APRX de servicio en Template.")
    log(f"[INFO] APRX de servicio a procesar ({len(aprx_servicios)}): {aprx_servicios}")

    # ------------------------------------------------------------------
    # Preparar APRX base (copiar plantilla vacia; arcpy.mp no crea vacios)
    # ------------------------------------------------------------------
    if os.path.exists(aprx_maestro_salida):
        os.remove(aprx_maestro_salida)
    shutil.copyfile(plantilla_base, aprx_maestro_salida)
    log(f"[OK] Plantilla base copiada a maestro: {aprx_maestro_salida}")

    aprx_maestro = arcpy.mp.ArcGISProject(aprx_maestro_salida)
    mapa_maestro = aprx_maestro.listMaps(nombre_mapa)[0]

    # ------------------------------------------------------------------
    # Acumuladores de estado para el resumen y el codigo de salida
    # ------------------------------------------------------------------
    hubo_capa_rota = False
    aprx_con_error_apertura = []      # APRX que no se pudieron abrir/procesar
    capas_rotas_total = []            # [(aprx, longName_capa)]
    capas_excluidas_total = []        # [(aprx, nombre_capa)]
    grupos_creados = 0
    reference_scale_aplicada = None   # se hereda del primer APRX de servicio

    # ------------------------------------------------------------------
    # Loop por APRX de servicio (try/except por APRX: uno corrupto no aborta
    # el resto). Nota: la capa rota NO cae aca (saveACopy no falla); se
    # detecta con isBroken mas abajo.
    # ------------------------------------------------------------------
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

        # Heredar la reference scale del mapa de servicio al maestro (una vez,
        # del primer APRX). Es propiedad del MAPA (no viaja en el .lyrx); sin
        # ella los simbolos dependientes de escala no escalan en el maestro.
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
        for elem in elementos:
            # --- Exclusion por nombre ---
            if elem.name in capas_excluidas:
                log(f"[EXCLUIDO] {elem.name} (en CAPAS_EXCLUIDAS_APPOFFLINE)")
                capas_excluidas_total.append((nombre_aprx, elem.name))
                continue

            # --- Deteccion proactiva de capas rotas (saveACopy no falla) ---
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
                    log(f"[ERROR export] {elem.name}: .lyrx no generado o vacio")
                    continue
                tam = os.path.getsize(destino)
                tipo = "GRUPO" if elem.isGroupLayer else "CAPA"
                log(f"[EXPORT] [{tipo}] {elem.name} -> {os.path.basename(destino)} ({tam} bytes)")
            except Exception as e:
                log(f"[ERROR export] {elem.name}: {repr(e)}")
                continue

            # --- Reimportar el .lyrx dentro del grupo contenedor (BOTTOM) ---
            try:
                lf = arcpy.mp.LayerFile(destino)
                mapa_maestro.addLayerToGroup(grupo_cont, lf, "BOTTOM")
                log(f"[IMPORT] {elem.name} -> grupo {nombre_sin_ext} (BOTTOM)")
            except Exception as e:
                log(f"[ERROR import] {elem.name}: {repr(e)}")
                continue

    # ------------------------------------------------------------------
    # Guardar el maestro
    # ------------------------------------------------------------------
    aprx_maestro.save()
    log("-" * 70)
    log(f"[OK] APRX maestro guardado: {aprx_maestro_salida}")

    # ------------------------------------------------------------------
    # Resumen y subtipo de error de TPK aportado por esta fase
    # ------------------------------------------------------------------
    subtipo = TPK_CAPA_ROTA if hubo_capa_rota else TPK_OK

    log("=" * 70)
    log("RESUMEN CONSTRUCCION LYRX MAESTRO")
    log(f"  APRX procesados        : {len(aprx_servicios)}")
    log(f"  Grupos creados         : {grupos_creados}")
    log(f"  APRX con error apertura: {len(aprx_con_error_apertura)} {aprx_con_error_apertura or ''}")
    log(f"  Capas excluidas        : {len(capas_excluidas_total)}")
    log(f"  Capas ROTAS detectadas : {len(capas_rotas_total)}")
    for aprx_o, capa in capas_rotas_total:
        log(f"      - {aprx_o} :: {capa}")
    log(f"  Reference scale        : 1:{reference_scale_aplicada}")
    log(f"  Subtipo error TPK      : {subtipo} "
        f"({'OK' if subtipo == 0 else 'CAPA ROTA'})")
    log("=" * 70)

    resumen = {
        "aprx_procesados": len(aprx_servicios),
        "grupos_creados": grupos_creados,
        "aprx_error_apertura": aprx_con_error_apertura,
        "capas_rotas": capas_rotas_total,
        "capas_excluidas": capas_excluidas_total,
        "hubo_capa_rota": hubo_capa_rota,
        "reference_scale": reference_scale_aplicada,
    }
    return aprx_maestro_salida, subtipo, resumen


def preparar_aprx_region(
    aprx_maestro,             # ruta del APRX maestro ya construido
    salida_dir,               # carpeta de salida
    region,                   # nombre de la subregion
    tpk_path_VRED_BASEMAP,    # ruta al .tpk VRED de la subregion
    nombre_mapa,              # "Mapa"
    log,                      # funcion de logging
):
    """Arma el APRX de una subregion partiendo del maestro: copia el maestro
    como APRX_{region}, agrega el TPK VRED como capa suelta al fondo (es
    basemap, no consultable) y guarda. Devuelve la ruta del APRX de region.

    Reemplaza el armado anterior que abria APRX_APP_OFFLINE.aprx. Ya no busca
    la capa TRANSFORMADOR ni hace moveLayer relativo a ella: el VRED va al
    fondo del TOC. La reference scale se hereda al copiar el maestro.
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
        # Elementos de primer nivel (para mover el TPK debajo del ultimo)
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
    log(f"[APRX] Generado APRX_{region}.aprx")
    return ruta_region

