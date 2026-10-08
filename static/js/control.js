import { urlWs } from './captura.js';

const $ = (sel) => document.querySelector(sel);
const ESTADOS = {
  preparada: 'Lista para iniciar',
  grabando: '● Grabando',
  pausada: 'En pausa',
  terminando: 'Terminando…',
  terminada: 'Terminada',
};
let ws = null;
let estado = null;

function hms(s) {
  s = Math.floor(s);
  const p = (n) => String(n).padStart(2, '0');
  return `${p(Math.floor(s / 3600))}:${p(Math.floor(s / 60) % 60)}:${p(s % 60)}`;
}

function esc(t) {
  return String(t ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}

function mostrarError(texto) {
  const el = $('#error-general');
  el.textContent = texto;
  el.classList.toggle('oculto', !texto);
  if (texto) setTimeout(() => { if (el.textContent === texto) mostrarError(''); }, 6000);
}

function orden(o, extra = {}) {
  if (ws?.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ orden: o, ...extra }));
}

function pintar() {
  const s = estado?.sesion;
  const abierta = s && s.estado !== 'terminada';
  $('#crear').classList.toggle('oculto', !!abierta);
  $('#sesion').classList.toggle('oculto', !s);

  if (s) {
    $('#ses-titulo').textContent = [s.curso, s.tema].filter(Boolean).join(' — ');
    $('#ses-carpeta').textContent = s.carpeta ? `Guardando en ${s.carpeta}/` : 'La carpeta se crea al iniciar.';
    const ins = $('#ses-estado');
    ins.textContent = ESTADOS[s.estado] ?? s.estado;
    ins.className = `insignia ${s.estado}`;
    $('#ses-tiempo').textContent = hms(s.grabando_s);
    $('#ses-total').textContent = s.total_s > s.grabando_s + 1
      ? `Grabado (sin pausas) · ${hms(s.total_s)} desde el inicio` : 'Grabado';

    const iniciar = $('#btn-iniciar');
    iniciar.textContent = s.estado === 'pausada' ? '▶ Reanudar' : '▶ Iniciar grabación';
    iniciar.disabled = !['preparada', 'pausada'].includes(s.estado);
    $('#btn-pausar').disabled = s.estado !== 'grabando';
    $('#btn-marca').disabled = !['grabando', 'pausada'].includes(s.estado);
    $('#btn-terminar').disabled = !['preparada', 'grabando', 'pausada'].includes(s.estado);
    $('#controles').classList.toggle('oculto', s.estado === 'terminada');

    $('#marcas').classList.toggle('oculto', !s.marcas.length);
    $('#marcas-lista').innerHTML = s.marcas
      .map((m) => `<li>${esc(m.hora.slice(11, 23))}${m.etiqueta ? ' — ' + esc(m.etiqueta) : ''}</li>`).join('');

    $('#resultado').classList.toggle('oculto', !s.resultado);
    if (s.resultado) {
      $('#resultado-filas').innerHTML = s.resultado.map((r) => {
        const dif = r.duracion_s - r.esperada_s;
        return `<tr>
          <td><strong>${esc(r.nombre)}</strong></td>
          <td class="${r.ok ? 'ok' : 'mal'}">${r.ok ? '✓' : '✗'} ${r.frecuencia_hz} Hz, ${r.canales === 1 ? 'mono' : r.canales + ' canales'}, ${esc(r.subtipo)}</td>
          <td>${r.duracion_s.toFixed(2)} s</td>
          <td>${r.esperada_s.toFixed(2)} s</td>
          <td class="${Math.abs(dif) > 1 ? 'mal' : ''}">${dif >= 0 ? '+' : ''}${dif.toFixed(2)} s</td>
          <td>${r.pausas_s.toFixed(1)} s</td>
          <td class="${r.huecos_s > 0 ? 'mal' : ''}">${r.huecos_s.toFixed(2)} s</td>
          <td class="${r.ceros_pct > 5 ? 'mal' : ''}">${r.ceros_pct} %</td>
        </tr>`;
      }).join('');
    }
  } else {
    $('#resultado').classList.add('oculto');
    $('#marcas').classList.add('oculto');
  }

  pintarVoz(estado?.voz, s);

  const disp = estado?.dispositivos ?? [];
  $('#sin-dispositivos').classList.toggle('oculto', disp.length > 0);
  $('#dispositivos').innerHTML = disp.map((d) => {
    const nivel = d.nivel_db == null ? 0 : Math.max(0, Math.min(100, (d.nivel_db + 60) / 60 * 100));
    const avisos = [];
    if (!d.conectado) avisos.push('Desconectado');
    if (d.ceros != null && d.ceros > 0.3) avisos.push(`⚠ El celular silencia el audio (${Math.round(d.ceros * 100)} % en cero)`);
    if (d.sin_audio_s != null && d.sin_audio_s > 2) avisos.push(`Sin audio hace ${d.sin_audio_s.toFixed(0)} s`);
    if (d.pendientes_s > 1) avisos.push(`${d.pendientes_s.toFixed(0)} s por enviar`);
    if (d.bateria && d.bateria.nivel <= 20 && !d.bateria.cargando) avisos.push(`Batería ${d.bateria.nivel} %`);
    if (d.huecos_s > 0) avisos.push(`Huecos: ${d.huecos_s.toFixed(1)} s`);
    const bat = d.bateria ? `🔋 ${d.bateria.nivel} %${d.bateria.cargando ? ' ⚡' : ''}` : '';
    return `<div class="tarjeta disp ${d.conectado ? '' : 'apagado'}">
      <div class="cabecera-panel">
        <strong>${d.grabando ? '<span class="punto-rec"></span>' : ''}${esc(d.nombre)}</strong>
        <span class="suave">${esc(bat)}</span>
      </div>
      <div class="suave">${esc(d.modelo ?? 'modelo desconocido')}</div>
      <div class="medidor ${d.pico > 0.98 ? 'satura' : ''}"><div style="width:${nivel}%"></div></div>
      <div class="fila-dato"><span class="suave">${d.nivel_db == null ? '—' : d.nivel_db.toFixed(0) + ' dBFS'}</span><span>${hms(d.recibido_s)} recibido</span></div>
      ${avisos.length ? `<div class="mal pequeño">${avisos.map(esc).join(' · ')}</div>` : ''}
    </div>`;
  }).join('');
}

function mmss(seg) {
  if (seg == null) return '—';
  const p = (n) => String(n).padStart(2, '0');
  return `${p(Math.floor(seg / 60))}:${p(Math.floor(seg) % 60)}`;
}

function pintarVoz(v, s) {
  $('#voz').classList.toggle('oculto', !v);
  if (!v) return;
  const presentes = v.alumnos.filter((a) => a.presente).length;
  $('#voz-presentes').textContent = `${presentes} / ${v.alumnos.length} presentes`;
  $('#voz-presentes').className = `insignia ${presentes ? 'terminada' : ''}`;
  $('#voz-mensaje').textContent = v.mensaje;
  $('#voz-mensaje').className = v.estado === 'listo' ? 'suave' : 'mal';
  $('#voz-alumnos').innerHTML = v.alumnos.map((a) => `<tr>
      <td><strong>${esc(a.nombre)}</strong> <span class="suave">${esc(a.codigo)}</span></td>
      <td class="${a.presente ? 'ok' : 'suave'}">${a.presente ? '✓ Presente' : (s && s.estado !== 'preparada' ? 'Sin oír aún' : '—')}</td>
      <td>${a.presente ? `${esc(a.hora)} <span class="suave">(min ${mmss(a.primera_vez_s)})</span>` : ''}</td>
      <td>${a.presente ? a.veces : ''}</td>
      <td>${a.presente ? a.mejor_similitud.toFixed(2) : ''}</td>
    </tr>`).join('');
  $('#voz-ultimos').innerHTML = v.ultimos.length ? v.ultimos.map((u) => `<tr>
      <td>${esc(u.hora)}</td><td>${esc(u.mic)}</td><td>${mmss(u.inicio_clase_s)}</td>
      <td class="${u.nombre === 'desconocido' ? 'suave' : 'ok'}">${esc(u.nombre)}</td>
      <td>${u.similitud.toFixed(2)}</td><td>${u.latencia_s.toFixed(1)} s</td>
    </tr>`).join('') : '<tr><td colspan="6" class="suave">Todavía no se ha oído ninguna voz.</td></tr>';
  $('#voz-latencia').textContent = v.segmentos
    ? `${v.segmentos} segmentos de voz · demora media ${v.latencia_media_s} s, máxima ${v.latencia_max_s} s`
      + (v.en_cola > 20 ? ` · ⚠ ${v.en_cola} trozos esperando` : '')
    : '';
}

function conectar() {
  ws = new WebSocket(urlWs('/ws/control'));
  ws.onopen = () => {
    $('#conexion-panel').textContent = 'Conectado';
    $('#conexion-panel').className = 'estado ok';
  };
  ws.onmessage = (e) => {
    const m = JSON.parse(e.data);
    if (m.tipo === 'error') mostrarError(m.mensaje);
    else if (m.tipo === 'estado') { estado = m; pintar(); }
  };
  ws.onclose = (e) => {
    $('#conexion-panel').textContent = 'Sin conexión';
    $('#conexion-panel').className = 'estado mal';
    if (e.code === 4403) {
      mostrarError('El panel solo funciona abierto desde la laptop.');
      return;
    }
    setTimeout(conectar, 1500);
  };
}

$('#url-clase').textContent = `${location.origin.replace('localhost', '10.42.0.1')}/clase`;
$('#btn-crear').addEventListener('click', () => orden('crear', { curso: $('#curso').value, tema: $('#tema').value }));
$('#btn-iniciar').addEventListener('click', () => orden('iniciar'));
$('#btn-pausar').addEventListener('click', () => orden('pausar'));
$('#btn-marca').addEventListener('click', () => {
  orden('marca', { etiqueta: $('#marca-etiqueta').value });
  $('#marca-etiqueta').value = '';
});
$('#btn-terminar').addEventListener('click', () => {
  if (confirm('¿Terminar la clase? Se cierran y verifican los archivos de todos los celulares.')) orden('terminar');
});
conectar();
