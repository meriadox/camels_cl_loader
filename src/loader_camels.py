"""
loader_camels.py
================================================================================
Descarga y lee CAMELS-CL v1.0: 516 cuencas chilenas con series diarias
de PP, T, Q y ETP derivadas de CR2MET + DGA.

Referencia
----------
Alvarez-Garreton et al. (2018). The CAMELS-CL dataset. HESS 22(11).
DOI dataset: 10.1594/PANGAEA.894885

Formato de los archivos
-----------------------
TSV tab-separated, wide:
  - Atributos  : filas = nombre_atributo, columnas = gauge_id
  - Series     : filas = fecha (YYYY-MM-DD), columnas = gauge_id
Missing value : " " (espacio entre comillas) → NaN vía pd.to_numeric

Variables disponibles (claves internas)
---------------------------------------
  'precip_cr2'   Precipitación diaria [mm]   (CR2MET)
  'tmean_cr2'    Temperatura media diaria [°C]  (CR2MET)
  'tmin_cr2'     Temperatura mínima diaria [°C] (CR2MET)
  'tmax_cr2'     Temperatura máxima diaria [°C] (CR2MET)
  'swe'          SWE diario [mm]
  'q_m3s'        Caudal observado diario [m³/s]  (DGA)
  'q_mm'         Caudal observado diario [mm]

Funciones públicas
------------------
    listar_cuencas      Catálogo de 516 cuencas con coordenadas y área
    cargar_atributos    70+ atributos de cuenca para uno o todos los gauges
    cargar_forzantes    PP + T diarios para un gauge_id
    cargar_caudal       Q diario [m³/s] para un gauge_id
    descargar_dataset   Descarga selectiva de archivos al cache_dir
"""

from __future__ import annotations

import zipfile
from pathlib import Path
from typing import List, Optional, Union
from urllib.request import urlretrieve

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Catálogo de archivos en PANGAEA
# ---------------------------------------------------------------------------
_BASE_URL = "https://store.pangaea.de/Publications/Alvarez-Garreton-etal_2018/"

_ARCHIVOS: dict[str, str] = {
    "atributos":   "1_CAMELScl_attributes.zip",
    "q_m3s":       "2_CAMELScl_streamflow_m3s.zip",
    "q_mm":        "3_CAMELScl_streamflow_mm.zip",
    "precip_cr2":  "4_CAMELScl_precip_cr2met.zip",
    "tmin_cr2":    "8_CAMELScl_tmin_cr2met.zip",
    "tmax_cr2":    "9_CAMELScl_tmax_cr2met.zip",
    "tmean_cr2":   "10_CAMELScl_tmean_cr2met.zip",
    "etp_har":     "12_CAMELScl_pet_hargreaves.zip",
    "swe":         "13_CAMELScl_swe.zip",
}

# Nombre del .txt dentro de cada zip
_TXT_DENTRO: dict[str, str] = {k: v.replace(".zip", ".txt") for k, v in _ARCHIVOS.items()}

# Mapeo clave interna → nombre de columna en el DataFrame de salida
_NOMBRE_COL: dict[str, str] = {
    "precip_cr2": "pp",
    "tmin_cr2":   "tmin",
    "tmax_cr2":   "tmax",
    "tmean_cr2":  "tmean",
    "etp_har":    "etp",
    "swe":        "swe",
}


# ---------------------------------------------------------------------------
# Descarga y caché
# ---------------------------------------------------------------------------
def descargar_dataset(
    cache_dir: Union[str, Path],
    variables: Optional[List[str]] = None,
    verbose: bool = True,
) -> None:
    """
    Descarga archivos CAMELS-CL a cache_dir (solo si no existen).

    Parameters
    ----------
    cache_dir : directorio donde guardar los zips
    variables : claves a descargar; None = todas.
                Opciones: 'atributos', 'q_m3s', 'q_mm', 'precip_cr2',
                          'tmin_cr2', 'tmax_cr2', 'tmean_cr2', 'swe'
    verbose   : True → imprime progreso
    """
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    keys = variables if variables is not None else list(_ARCHIVOS)
    for key in keys:
        if key not in _ARCHIVOS:
            raise ValueError(f"Variable '{key}' no reconocida. Opciones: {list(_ARCHIVOS)}")
        dest = cache_dir / _ARCHIVOS[key]
        if not dest.exists():
            url = _BASE_URL + _ARCHIVOS[key]
            if verbose:
                print(f"[CAMELS] Descargando {_ARCHIVOS[key]} …")
            urlretrieve(url, dest)
            if verbose:
                size_mb = dest.stat().st_size / 1e6
                print(f"         {size_mb:.1f} MB guardados en {dest}")
        elif verbose:
            print(f"[CAMELS] {_ARCHIVOS[key]} ya en caché")


# ---------------------------------------------------------------------------
# Lectura interna
# ---------------------------------------------------------------------------
def _leer_tsv(cache_dir: Path, key: str, verbose: bool = True) -> pd.DataFrame:
    """
    Lee el TSV dentro del zip, descargando si es necesario.

    Retorna DataFrame crudo: para series → DatetimeIndex × gauge_id;
    para atributos → nombre_atributo × gauge_id (transponer para uso normal).
    """
    dest = cache_dir / _ARCHIVOS[key]
    if not dest.exists():
        descargar_dataset(cache_dir, variables=[key], verbose=verbose)

    txt = _TXT_DENTRO[key]
    with zipfile.ZipFile(dest) as zf:
        with zf.open(txt) as f:
            df = pd.read_csv(
                f,
                sep="\t",
                index_col=0,
                na_values=[" ", ""],   # " " es el missing value de CAMELS-CL
                keep_default_na=True,
            )
    # Limpiar espacios en nombres de índice y columnas
    df.index = df.index.str.strip() if hasattr(df.index, "str") else df.index
    df.columns = df.columns.str.strip()
    return df


def _normalizar_id(gauge_id: Union[str, int]) -> str:
    """Normaliza gauge_id a string de entero limpio (sin espacios)."""
    return str(int(str(gauge_id).strip()))


def _extraer_gauge(df: pd.DataFrame, gauge_id: Union[str, int]) -> pd.Series:
    """Extrae columna de un gauge del DataFrame wide. Lanza KeyError si no existe."""
    gid = _normalizar_id(gauge_id)
    if gid not in df.columns:
        raise KeyError(
            f"gauge_id '{gid}' no encontrado en el dataset. "
            "Usa listar_cuencas() para ver los IDs disponibles."
        )
    s = df[gid].copy()
    s.name = gid
    return s


def _parsear_serie_temporal(
    s: pd.Series,
    inicio: Optional[str],
    fin: Optional[str],
) -> pd.Series:
    """Convierte índice string → DatetimeIndex, valores a float, y recorta."""
    s.index = pd.to_datetime(s.index, format="%Y-%m-%d", errors="coerce")
    s.index.name = "date"
    s = pd.to_numeric(s, errors="coerce")
    s = s[s.index.notna()]
    if inicio:
        s = s.loc[inicio:]
    if fin:
        s = s.loc[:fin]
    return s


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------
def listar_cuencas(
    cache_dir: Union[str, Path],
    verbose: bool = False,
) -> pd.DataFrame:
    """
    Devuelve catálogo de cuencas CAMELS-CL.

    Parameters
    ----------
    cache_dir : directorio cache

    Returns
    -------
    DataFrame con índice gauge_id (str) y columnas:
        nombre, lat, lon, area_km2, elev_media_m,
        periodo_inicio, periodo_fin, n_obs
    """
    cache_dir = Path(cache_dir)
    df_raw = _leer_tsv(cache_dir, "atributos", verbose=verbose)
    df = df_raw.T.copy()
    df.index.name = "gauge_id"

    cols_map = {
        "gauge_name":          "nombre",
        "gauge_lat":           "lat",
        "gauge_lon":           "lon",
        "area":                "area_km2",
        "elev_mean":           "elev_media_m",
        "record_period_start": "periodo_inicio",
        "record_period_end":   "periodo_fin",
        "n_obs":               "n_obs",
    }
    disponibles = {k: v for k, v in cols_map.items() if k in df.columns}
    out = df[list(disponibles)].rename(columns=disponibles)
    for col in ["lat", "lon", "area_km2", "elev_media_m"]:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")
    return out


def cargar_atributos(
    cache_dir: Union[str, Path],
    gauge_id: Optional[Union[str, int]] = None,
    verbose: bool = False,
) -> pd.DataFrame:
    """
    Lee todos los atributos de cuenca (70+ variables).

    Parameters
    ----------
    cache_dir : directorio cache
    gauge_id  : si se indica, retorna solo esa cuenca (1 fila); None → todas

    Returns
    -------
    DataFrame con índice gauge_id y una columna por atributo.
    """
    cache_dir = Path(cache_dir)
    df_raw = _leer_tsv(cache_dir, "atributos", verbose=verbose)
    df = df_raw.T.copy()
    df.index.name = "gauge_id"

    if gauge_id is not None:
        gid = _normalizar_id(gauge_id)
        if gid not in df.index:
            raise KeyError(f"gauge_id '{gid}' no encontrado.")
        return df.loc[[gid]]
    return df


def _ruta_parquet(datos_dir: Path, nombre: str) -> Path:
    return datos_dir / f"{nombre}.parquet"


def cargar_forzantes(
    cache_dir: Union[str, Path],
    gauge_id: Union[str, int],
    inicio: Optional[str] = None,
    fin: Optional[str] = None,
    variables: List[str] = ("precip_cr2", "tmean_cr2"),
    verbose: bool = True,
    datos_dir: Optional[Union[str, Path]] = None,
) -> pd.DataFrame:
    """
    Carga series diarias de forzantes meteorológicos para una cuenca.

    Parameters
    ----------
    cache_dir : directorio con los zips descargados de PANGAEA
    gauge_id  : ID de estación fluviométrica DGA (ej. 5410002)
    inicio    : 'YYYY-MM-DD' o None (desde inicio del registro, 1979-01-01)
    fin       : 'YYYY-MM-DD' o None (hasta fin del registro)
    variables : claves a cargar; subset de:
                {'precip_cr2', 'tmin_cr2', 'tmax_cr2', 'tmean_cr2', 'etp_har', 'swe'}
    datos_dir : si se indica, guarda/lee un Parquet en datos_dir/
                forzantes_{gauge_id}_{inicio[:4]}_{fin[:4]}.parquet
                En corridas posteriores, lee directo del Parquet sin tocar los zips.

    Returns
    -------
    DataFrame con DatetimeIndex diario y columnas: 'pp', 'tmean', 'tmin', 'tmax',
    'etp', 'swe' (según variables solicitadas). NaN donde no hay dato.
    """
    gid = _normalizar_id(gauge_id)
    ini_tag = (inicio or "0000")[:4]
    fin_tag = (fin   or "9999")[:4]

    # ── Leer desde Parquet si existe ─────────────────────────────────────────
    if datos_dir is not None:
        datos_dir = Path(datos_dir)
        parquet = _ruta_parquet(datos_dir, f"forzantes_{gid}_{ini_tag}_{fin_tag}")
        if parquet.exists():
            df = pd.read_parquet(parquet)
            needed = [_NOMBRE_COL[k] for k in variables if k in _NOMBRE_COL]
            if all(c in df.columns for c in needed):
                if verbose:
                    print(f"[CAMELS] forzantes leídas desde {parquet.name}")
                return df[needed]

    # ── Extraer desde zips ────────────────────────────────────────────────────
    cache_dir = Path(cache_dir)
    series: dict[str, pd.Series] = {}
    for key in variables:
        if key not in _NOMBRE_COL:
            raise ValueError(
                f"Variable '{key}' no válida para forzantes. "
                f"Opciones: {list(_NOMBRE_COL)}"
            )
        df_raw = _leer_tsv(cache_dir, key, verbose=verbose)
        s = _extraer_gauge(df_raw, gauge_id)
        s = _parsear_serie_temporal(s, inicio, fin)
        s.name = _NOMBRE_COL[key]
        series[s.name] = s

    if not series:
        return pd.DataFrame()

    df = pd.DataFrame(series)

    # ── Guardar Parquet ───────────────────────────────────────────────────────
    if datos_dir is not None:
        datos_dir.mkdir(parents=True, exist_ok=True)
        df.to_parquet(parquet)
        if verbose:
            print(f"[CAMELS] forzantes guardadas en {parquet}")

    return df


def cargar_caudal(
    cache_dir: Union[str, Path],
    gauge_id: Union[str, int],
    inicio: Optional[str] = None,
    fin: Optional[str] = None,
    unidades: str = "m3s",
    verbose: bool = True,
    datos_dir: Optional[Union[str, Path]] = None,
) -> pd.Series:
    """
    Carga serie diaria de caudal observado para una cuenca.

    Parameters
    ----------
    cache_dir : directorio con los zips de PANGAEA
    gauge_id  : ID de estación DGA (ej. 5410002)
    inicio    : 'YYYY-MM-DD' o None
    fin       : 'YYYY-MM-DD' o None
    unidades  : 'm3s' (m³/s, default) | 'mm' (mm/día normalizado por área)
    datos_dir : si se indica, guarda/lee un Parquet en datos_dir/
                q_{unidades}_{gauge_id}_{inicio[:4]}_{fin[:4]}.parquet

    Returns
    -------
    Series con DatetimeIndex diario y name='q_m3s' o 'q_mm'.
    NaN donde no hay datos observados.
    """
    if unidades not in ("m3s", "mm"):
        raise ValueError("unidades debe ser 'm3s' o 'mm'")

    gid = _normalizar_id(gauge_id)
    ini_tag = (inicio or "0000")[:4]
    fin_tag = (fin   or "9999")[:4]
    col_name = f"q_{unidades}"

    # ── Leer desde Parquet si existe ─────────────────────────────────────────
    if datos_dir is not None:
        datos_dir = Path(datos_dir)
        parquet = _ruta_parquet(datos_dir, f"{col_name}_{gid}_{ini_tag}_{fin_tag}")
        if parquet.exists():
            s = pd.read_parquet(parquet).iloc[:, 0]
            s.name = col_name
            if verbose:
                print(f"[CAMELS] caudal leído desde {parquet.name}")
            return s

    # ── Extraer desde zip ─────────────────────────────────────────────────────
    cache_dir = Path(cache_dir)
    key = f"q_{unidades}"
    df_raw = _leer_tsv(cache_dir, key, verbose=verbose)
    s = _extraer_gauge(df_raw, gauge_id)
    s = _parsear_serie_temporal(s, inicio, fin)
    s.name = col_name

    # ── Guardar Parquet ───────────────────────────────────────────────────────
    if datos_dir is not None:
        datos_dir.mkdir(parents=True, exist_ok=True)
        s.to_frame().to_parquet(parquet)
        if verbose:
            print(f"[CAMELS] caudal guardado en {parquet}")

    return s


def guardar_atributos_json(
    datos_dir: Union[str, Path],
    cache_dir: Union[str, Path],
    gauge_id: Union[str, int],
    verbose: bool = True,
) -> Path:
    """
    Guarda atributos de una cuenca como JSON en datos_dir.

    El archivo se llama atributos_{gauge_id}.json y contiene todos los
    atributos de la cuenca (70+ variables) en formato serializable.

    Parameters
    ----------
    datos_dir : directorio de salida
    cache_dir : directorio con los zips de PANGAEA
    gauge_id  : ID de estación DGA

    Returns
    -------
    Path al archivo JSON creado.
    """
    import json as _json

    gid = _normalizar_id(gauge_id)
    datos_dir = Path(datos_dir)
    dest = datos_dir / f"atributos_{gid}.json"

    if dest.exists():
        if verbose:
            print(f"[CAMELS] atributos ya en {dest.name}")
        return dest

    df_attr = cargar_atributos(cache_dir, gauge_id=gid, verbose=verbose)
    attrs = df_attr.loc[gid].to_dict()

    datos_dir.mkdir(parents=True, exist_ok=True)
    dest.write_text(_json.dumps(attrs, ensure_ascii=False, indent=2), encoding="utf-8")
    if verbose:
        print(f"[CAMELS] atributos guardados en {dest}")
    return dest
