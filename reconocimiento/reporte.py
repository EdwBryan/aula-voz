"""Reporte de una clase en HTML (se abre en el navegador, sin internet).

    python -m reconocimiento.reporte [carpeta_clase]   # → reporte.html en la carpeta de la clase

Junta lo que dejaron las etapas: calidad del audio de cada celular, asistencia, voces
(hablantes.json), línea de tiempo, similitudes y latencia en vivo, más la última
evaluación de data/metricas/. Tiene nombres de alumnos: se queda en la laptop.
"""

import html
import json
from pathlib import Path

import numpy as np
import soundfile as sf

from .clase import carpeta_desde_args, metadata
from .config import FS, METRICAS

COLORES = ["#2563eb", "#d97706", "#059669", "#db2777", "#7c3aed", "#0891b2", "#65a30d", "#dc2626"]


def leer(carpeta: Path, nombre: str):
    f = carpeta / nombre
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None


def e(t) -> str:
    return html.escape(str(t))


def mmss(s) -> str:
    if s is None:
        return "—"
    s = int(s)
    return f"{s // 3600:d}:{s // 60 % 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60:d}:{s % 60:02d}"


def nivel_mic(ruta: Path) -> dict:
    """Ruido de fondo y nivel de voz típico (dBFS) en tramas de 20 ms."""
    medianas, p95 = [], []
    for b in sf.blocks(ruta, blocksize=FS * 30, dtype="float32"):
        b = b[: len(b) // 320 * 320]
        if len(b) == 0:
            continue
        db = 20 * np.log10(np.sqrt((b.reshape(-1, 320) ** 2).mean(1)) + 1e-9)
        db = db[db > -100]  # sin los huecos rellenos con ceros
        if len(db):
            medianas.append(np.percentile(db, 10))
            p95.append(np.percentile(db, 95))
    return {"fondo_db": round(float(np.median(medianas)), 1) if medianas else None,
            "fuerte_db": round(float(np.median(p95)), 1) if p95 else None}


def linea_tiempo(voces: list[dict], duracion: float) -> str:
    if not voces or duracion <= 0:
        return "<p class='suave'>No se detectaron voces.</p>"
    ancho, alto_fila, izq = 900, 26, 230
    alto = alto_fila * len(voces) + 30
    partes = [f'<svg viewBox="0 0 {ancho} {alto}" class="tiempo" role="img" aria-label="Línea de tiempo de voces">']
    paso = max(60, int(duracion / 10 / 60 + 1) * 60)
    for t in range(0, int(duracion) + 1, paso):
        x = izq + (ancho - izq - 10) * t / duracion
        partes.append(f'<line x1="{x:.1f}" y1="0" x2="{x:.1f}" y2="{alto - 20}" class="rejilla"/>'
                      f'<text x="{x:.1f}" y="{alto - 6}" class="eje" text-anchor="middle">{mmss(t)}</text>')
    for i, v in enumerate(voces):
        y = i * alto_fila + 4
        color = COLORES[i % len(COLORES)] if "sin " not in v["voz"] else "var(--suave)"
        partes.append(f'<text x="{izq - 8}" y="{y + 15}" class="etiqueta" text-anchor="end">{e(v["voz"][:30])}</text>')
        for a, b in v["momentos"]:
            x = izq + (ancho - izq - 10) * a / duracion
            w = max(1.5, (ancho - izq - 10) * (b - a) / duracion)
            partes.append(f'<rect x="{x:.1f}" y="{y}" width="{w:.1f}" height="{alto_fila - 8}" rx="2" fill="{color}">'
                          f'<title>{e(v["voz"])}: {mmss(a)}–{mmss(b)}</title></rect>')
    partes.append("</svg>")
    return "".join(partes)


def histograma(segs: list[dict], umbral: float) -> str:
    if not segs:
        return ""
    bins = np.linspace(-0.2, 1.0, 25)
    rec = np.histogram([s["similitud"] for s in segs if s["codigo"]], bins)[0]
    des = np.histogram([s["similitud"] for s in segs if not s["codigo"]], bins)[0]
    tope = max(1, int((rec + des).max()))
    ancho, alto, abajo = 600, 180, 22
    bw = ancho / (len(bins) - 1)
    partes = [f'<svg viewBox="0 0 {ancho} {alto + abajo}" class="hist" role="img" aria-label="Histograma de similitudes">']
    for i in range(len(bins) - 1):
        hd = alto * des[i] / tope
        hr = alto * rec[i] / tope
        x = i * bw
        if des[i]:
            partes.append(f'<rect x="{x + 1:.1f}" y="{alto - hd:.1f}" width="{bw - 2:.1f}" height="{hd:.1f}" fill="var(--suave)"><title>{des[i]} desconocidos</title></rect>')
        if rec[i]:
            partes.append(f'<rect x="{x + 1:.1f}" y="{alto - hd - hr:.1f}" width="{bw - 2:.1f}" height="{hr:.1f}" fill="var(--primario)"><title>{rec[i]} reconocidos</title></rect>')
    xu = ancho * (umbral - bins[0]) / (bins[-1] - bins[0])
    partes.append(f'<line x1="{xu:.1f}" y1="0" x2="{xu:.1f}" y2="{alto}" class="umbral"/>'
                  f'<text x="{xu + 4:.1f}" y="12" class="eje">umbral {umbral}</text>')
    for v in (0.0, 0.25, 0.5, 0.75, 1.0):
        x = ancho * (v - bins[0]) / (bins[-1] - bins[0])
        partes.append(f'<text x="{x:.1f}" y="{alto + 16}" class="eje" text-anchor="{"end" if v == 1.0 else "middle"}">{v:.2f}</text>')
    partes.append("</svg>")
    return "".join(partes)


def ultima_evaluacion() -> dict | None:
    archivos = sorted(METRICAS.glob("*_evaluacion.json")) if METRICAS.exists() else []
    return json.loads(archivos[-1].read_text(encoding="utf-8")) if archivos else None


def main():
    carpeta = carpeta_desde_args()
    meta = metadata(carpeta)
    ident = leer(carpeta, "identificacion.json") or {"segmentos": [], "umbral": None}
    hab = leer(carpeta, "hablantes.json") or {"voces": []}
    agrupados = {(round(a, 3), round(b, 3)) for v in hab["voces"] for a, b in v["momentos"]}
    asis = leer(carpeta, "asistencia.json") or []
    vivo = leer(carpeta, "en_vivo.json")
    inter = leer(carpeta, "intervenciones.json") or []
    segs = ident["segmentos"]
    duracion = meta.get("tiempo_total_s") or max((s["fin_clase_s"] for s in segs), default=0)

    mics = []
    for n, d in meta["dispositivos"].items():
        v = d.get("verificacion") or {}
        niv = nivel_mic(carpeta / d["archivo"])
        voz = sum(s["fin_s"] - s["inicio_s"] for s in segs if s["mic"] == n)
        mics.append({"nombre": n, "duracion": v.get("duracion_s"), "ceros": v.get("ceros_pct"),
                     "huecos": d.get("huecos_total_s", 0), "voz": voz, **niv,
                     "segmentos": sum(1 for s in segs if s["mic"] == n)})

    presentes = sum(1 for a in asis if a["presente"])
    voz_total = sum(i["fin_s"] - i["inicio_s"] for i in inter)
    kpis = [
        ("Duración", mmss(duracion)),
        ("Celulares", len(mics)),
        ("Presentes", f"{presentes} / {len(asis)}"),
        ("Intervenciones", len(inter)),
        ("Voces distintas", len(hab["voces"])),
        ("Voz detectada", f"{mmss(voz_total)} ({100 * voz_total / duracion:.0f} %)" if duracion else "—"),
    ]
    if vivo and vivo.get("latencia_s"):
        kpis.append(("Demora en vivo (máx)", f"{vivo['latencia_s']['media']:.2f} s ({vivo['latencia_s']['max']:.2f})"))

    ev = ultima_evaluacion()
    filas_ev = ""
    if ev:
        for nombre, r in ev["pruebas"].items():
            a, b = r["A_identificacion"], r["B_extremo_a_extremo"]
            filas_ev += (f"<tr><td>{e(nombre)}</td><td>{r['anotados']}</td><td>{a['acierto_pct']} %</td>"
                         f"<td>{b['acierto_pct']} %</td><td>{100 * r['acierto_sin_umbral'] / r['anotados']:.0f} %</td></tr>")
            for k in [k for k in r if k.startswith("B_primeros_")]:
                n = k.split("_")[-1]
                a2 = r[f"A_primeros_{n}"]
                filas_ev += (f"<tr><td>{e(nombre)}, parte confiable</td><td>{r[k]['total']}</td><td>{a2['acierto_pct']} %</td>"
                             f"<td>{r[k]['acierto_pct']} %</td><td>—</td></tr>")

    cuerpo = f"""
<header>
  <h1>{e(meta.get('curso'))}</h1>
  <p class="suave">{e(meta.get('tema'))} · {e((meta.get('inicio') or '')[:16].replace('T', ' '))} · clase <code>{e(carpeta.name)}</code></p>
</header>

<section class="kpis">{''.join(f'<div class="kpi"><span>{e(k)}</span><strong>{e(v)}</strong></div>' for k, v in kpis)}</section>

<section>
  <h2>Asistencia</h2>
  <table>
    <thead><tr><th>Alumno</th><th>Código</th><th>Estado</th><th>Primera vez</th><th>Intervenciones</th><th>Voz</th><th>Similitud máx.</th></tr></thead>
    <tbody>{''.join(f'''<tr><td>{e(a["nombre"])}</td><td>{e(a["codigo"])}</td>
      <td class="{'ok' if a['presente'] else 'suave'}">{'✓ Presente' if a['presente'] else 'No se le oyó'}</td>
      <td>{mmss(a["primera_vez_s"])}</td><td>{a["intervenciones"]}</td><td>{a["voz_s"]} s</td>
      <td>{f"{a['mejor_similitud']:.2f}" if a["mejor_similitud"] is not None else '—'}</td></tr>''' for a in asis) or '<tr><td colspan="7" class="suave">Sin huellas registradas.</td></tr>'}</tbody>
  </table>
</section>

<section>
  <h2>Quién habló y cuándo</h2>
  <p class="suave">Cada fila es una voz distinta (agrupadas por parecido, aunque no estén registradas). Las grises son personas sin registrar, por ejemplo el docente.</p>
  {linea_tiempo(hab['voces'] + [{"voz": "voces cortas sin agrupar", "momentos": [[i["inicio_s"], i["fin_s"]] for i in inter if (round(i["inicio_s"], 3), round(i["fin_s"], 3)) not in agrupados]}], duracion)}
  <table>
    <thead><tr><th>Voz</th><th>Intervenciones</th><th>Tiempo de voz</th><th>Primera</th><th>Última</th><th>Celulares</th><th></th></tr></thead>
    <tbody>{''.join(f'''<tr><td>{e(v["voz"])}</td><td>{v["intervenciones"]}</td><td>{mmss(v["voz_s"])}</td>
      <td>{mmss(v["primera_s"])}</td><td>{mmss(v["ultima_s"])}</td><td>{e(", ".join(f"{m} ({n})" for m, n in v["mics"].items()))}</td>
      <td class="mal">{('⚠ ' + e(v["alerta"])) if v["alerta"] else ''}</td></tr>''' for v in hab['voces'])}</tbody>
  </table>
  <p class="suave pequeño">{hab.get('sin_agrupar', 0)} intervenciones muy cortas quedaron sin agrupar. Umbral de misma voz: {hab.get('umbral_misma_voz', '—')}.</p>
</section>

<section>
  <h2>Similitud de cada segmento con la huella más parecida</h2>
  <p class="suave">Azul: reconocido como un alumno. Gris: desconocido (no pasa el umbral).</p>
  {histograma(segs, ident.get('umbral') or 0.3)}
</section>

<section>
  <h2>Calidad del audio por celular</h2>
  <table>
    <thead><tr><th>Celular</th><th>Duración</th><th>Ruido de fondo</th><th>Voz fuerte</th><th>Ceros</th><th>Huecos</th><th>Segmentos de voz</th><th>Voz</th></tr></thead>
    <tbody>{''.join(f'''<tr><td>{e(m["nombre"])}</td><td>{mmss(m["duracion"])}</td><td>{m["fondo_db"]} dBFS</td><td>{m["fuerte_db"]} dBFS</td>
      <td class="{'mal' if (m['ceros'] or 0) > 5 else ''}">{m["ceros"] if m["ceros"] is not None else '—'} %</td><td>{m["huecos"]} s</td>
      <td>{m["segmentos"]}</td><td>{mmss(m["voz"])}</td></tr>''' for m in mics)}</tbody>
  </table>
  <p class="suave pequeño">Ceros altos = el celular silencia el audio (compuerta de ruido) y el reconocimiento falla.</p>
</section>

{f'''<section>
  <h2>Precisión medida (audios anotados)</h2>
  <table>
    <thead><tr><th>Prueba</th><th>Anotados</th><th>Identificación</th><th>Completo (VAD + ECAPA)</th><th>Sin umbral</th></tr></thead>
    <tbody>{filas_ev}</tbody>
  </table>
  <p class="suave pequeño">Requisito: ≥ 70 %. Umbral {ev["calibracion"]["umbral"]}.</p>
</section>''' if ev else ''}

<footer class="suave pequeño">Silero VAD + SpeechBrain ECAPA (spkrec-ecapa-voxceleb), 16 kHz mono. Todo procesado en la laptop, sin internet.</footer>
"""
    pagina = f"""<!doctype html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Reporte de clase</title>
<style>
:root {{ --fondo:#f7f7f5; --tarjeta:#fff; --texto:#1c1c1c; --suave:#8a8a85; --borde:#e2e2dd; --primario:#2563eb; --ok:#059669; --mal:#dc2626; }}
@media (prefers-color-scheme: dark) {{ :root {{ --fondo:#141414; --tarjeta:#1e1e1e; --texto:#ececec; --suave:#8d8d88; --borde:#333; --primario:#60a5fa; --ok:#34d399; --mal:#f87171; }} }}
body {{ background:var(--fondo); color:var(--texto); font:16px/1.5 system-ui, sans-serif; margin:0; }}
main {{ max-width:1000px; margin:0 auto; padding:24px 16px 48px; }}
h1 {{ margin:0; font-size:1.7rem; }} h2 {{ font-size:1.15rem; margin:0 0 8px; }}
section {{ background:var(--tarjeta); border:1px solid var(--borde); border-radius:12px; padding:16px; margin:16px 0; overflow-x:auto; }}
.kpis {{ display:grid; grid-template-columns:repeat(auto-fit, minmax(140px, 1fr)); gap:10px; background:none; border:0; padding:0; }}
.kpi {{ background:var(--tarjeta); border:1px solid var(--borde); border-radius:12px; padding:12px; }}
.kpi span {{ display:block; color:var(--suave); font-size:.85rem; }} .kpi strong {{ font-size:1.3rem; font-variant-numeric:tabular-nums; }}
table {{ width:100%; border-collapse:collapse; font-size:.92rem; margin-top:8px; }}
th, td {{ text-align:left; padding:6px 8px; border-bottom:1px solid var(--borde); white-space:nowrap; }}
th {{ color:var(--suave); font-weight:600; }}
.suave {{ color:var(--suave); }} .pequeño {{ font-size:.85rem; }} .ok {{ color:var(--ok); font-weight:600; }} .mal {{ color:var(--mal); }}
svg {{ width:100%; height:auto; display:block; margin:8px 0; }}
.rejilla {{ stroke:var(--borde); }} .eje {{ fill:var(--suave); font-size:11px; }} .etiqueta {{ fill:var(--texto); font-size:12px; }}
.umbral {{ stroke:var(--mal); stroke-dasharray:4 3; }}
svg.hist {{ max-width:620px; }}
code {{ font-size:.85em; }}
</style></head>
<body><main>{cuerpo}</main></body></html>"""
    (carpeta / "reporte.html").write_text(pagina, encoding="utf-8")
    print(f"→ {carpeta / 'reporte.html'}")


if __name__ == "__main__":
    main()
