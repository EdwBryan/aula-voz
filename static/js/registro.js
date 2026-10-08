import { FS, Microfono, infoDispositivo, dbfs, wavBlob, unir, urlWs } from './captura.js';

// Mismos criterios que server/audio.py (el servidor vuelve a comprobar y decide).
const DURACION_TOMA_S = 5.0; // máximo: se detiene sola
const DURACION_MIN_S = 2.0;
const VOZ_MIN_S = 0.8;
const NIVEL_MIN_DBFS = -42;
const SATURACION_MAX = 0.005;
const TRAMA = 320;
const ESPERA_MICROFONO_MS = 600; // el micrófono recién abierto mete un "clic" al inicio

const $ = (sel, raiz = document) => raiz.querySelector(sel);
const mic = new Microfono();
let config = null;
let tomas = [];          // { pcm, ok, url, el }
let grabando = null;     // índice de la toma que se está grabando
let enviando = false;
let nombreFrases = null; // nombre con el que se armaron las frases

// ---------- utilidades ----------

function percentil(arr, p) {
  const o = Float64Array.from(arr).sort();
  const i = (o.length - 1) * p / 100;
  const a = Math.floor(i), b = Math.ceil(i);
  return o[a] + (o[b] - o[a]) * (i - a);
}

function evaluarToma(pcm) {
  const dur = pcm.length / FS;
  const n = Math.floor(pcm.length / TRAMA);
  if (n === 0) return { ok: false, motivo: 'La toma está vacía.' };
  const db = new Float64Array(n);
  let recortes = 0;
  for (let t = 0; t < n; t++) {
    let s = 0;
    for (let k = t * TRAMA; k < (t + 1) * TRAMA; k++) {
      const x = pcm[k] / 32768;
      s += x * x;
    }
    db[t] = 20 * Math.log10(Math.sqrt(s / TRAMA) + 1e-9);
  }
  for (const v of pcm) if (Math.abs(v) >= 32700) recortes++;
  const piso = percentil(db, 10);
  const fuerte = percentil(db, 95);
  const umbral = Math.max(piso + 12, -50);
  const vozS = db.filter((d) => d > umbral).length * TRAMA / FS;
  const sat = recortes / pcm.length;

  let motivo = null;
  if (dur < DURACION_MIN_S) motivo = `Muy corta (${dur.toFixed(1)} s). Lee la frase completa antes de detener.`;
  else if (fuerte < NIVEL_MIN_DBFS) motivo = 'Muy silenciosa. Habla más fuerte o acerca el celular.';
  else if (vozS < VOZ_MIN_S) motivo = 'Casi no se oye voz. Lee la frase completa en voz alta.';
  else if (sat > SATURACION_MAX) motivo = 'El sonido satura. Aleja un poco el celular de la boca.';
  return { ok: motivo === null, motivo };
}

const esperar = (ms) => new Promise((r) => setTimeout(r, ms));

function mostrarError(texto) {
  const el = $('#error-general');
  el.textContent = texto;
  el.classList.toggle('oculto', !texto);
  if (texto) el.scrollIntoView({ behavior: 'smooth', block: 'center' });
}

function datosAlumno() {
  return {
    nombre: $('#nombre').value.trim().replace(/\s+/g, ' '),
    codigo: $('#codigo').value.trim(),
    consentimiento: $('#consentimiento').checked,
  };
}

// ---------- paso 1: datos ----------

function validarDatos() {
  const d = datosAlumno();
  const ok = d.nombre.length >= 2 && /^[A-Za-z0-9-]{3,20}$/.test(d.codigo) && d.consentimiento;
  $('#continuar').disabled = !ok;
}

function irATomas() {
  const d = datosAlumno();
  // Si cambió el nombre, las tomas ya grabadas dicen otra frase: se empiezan de nuevo.
  if (nombreFrases !== null && nombreFrases !== d.nombre) {
    tomas.forEach((t) => t.url && URL.revokeObjectURL(t.url));
    crearTomas();
    actualizarBotones();
  }
  nombreFrases = d.nombre;
  tomas.forEach((t, i) => { $('.frase', t.el).textContent = config.frases[i].replace('{nombre}', d.nombre); });
  $('#paso-datos').classList.add('oculto');
  $('#paso-tomas').classList.remove('oculto');
  window.scrollTo(0, 0);
}

// ---------- paso 2: tomas ----------

function crearTomas() {
  const cont = $('#tomas');
  cont.innerHTML = '';
  tomas = [];
  for (let i = 0; i < config.frases.length; i++) {
    const el = $('#plantilla-toma').content.firstElementChild.cloneNode(true);
    $('.n', el).textContent = i + 1;
    $('.accion', el).addEventListener('click', () => (grabando === i ? detener(i) : grabar(i)));
    cont.appendChild(el);
    tomas.push({ pcm: null, ok: false, url: null, el });
  }
  $('#num-tomas').textContent = config.frases.length;
}

function pintarToma(i, { estado, clase = '', mensaje = '', claseMensaje = '' }) {
  const { el } = tomas[i];
  const e = $('.estado', el);
  e.textContent = estado;
  e.className = `estado ${clase}`;
  const m = $('.mensaje', el);
  m.textContent = mensaje;
  m.className = `mensaje ${claseMensaje}`;
}

function actualizarBotones() {
  tomas.forEach((t, i) => {
    const b = $('.accion', t.el);
    if (grabando === i) {
      b.textContent = '■ Detener';
      b.className = 'grabar accion';
      b.disabled = false;
    } else {
      b.textContent = t.pcm ? '↻ Repetir' : '● Grabar';
      b.className = t.pcm ? 'secundario accion' : 'grabar accion';
      b.disabled = grabando !== null || enviando;
    }
  });
  $('#enviar').disabled = grabando !== null || enviando || !tomas.every((t) => t.ok);
  $('#volver').disabled = grabando !== null || enviando;
}

async function grabar(i) {
  if (grabando !== null || enviando) return;
  mostrarError('');
  document.querySelectorAll('audio').forEach((a) => a.pause()); // que no se grabe la reproducción
  const t = tomas[i];

  if (!mic.ctx) {
    pintarToma(i, { estado: 'Preparando…' });
    try {
      await mic.abrir();
    } catch (e) {
      pintarToma(i, { estado: 'Sin micrófono', clase: 'mal' });
      mostrarError(e.name === 'NotAllowedError'
        ? 'No diste permiso para usar el micrófono. Actívalo en los permisos del sitio y vuelve a intentar.'
        : `No se pudo abrir el micrófono: ${e.message}`);
      return;
    }
    await esperar(ESPERA_MICROFONO_MS);
  }

  const objetivo = Math.round(DURACION_TOMA_S * FS);
  const trozos = [];
  let muestras = 0;
  grabando = i;
  t.trozos = trozos;
  $('.grabando', t.el).classList.remove('oculto');
  $('audio', t.el).classList.add('oculto');
  pintarToma(i, { estado: 'Grabando…', clase: 'mal', mensaje: 'Lee la frase ahora.' });
  actualizarBotones();

  const medidor = $('.medidor', t.el);
  const barra = $('.medidor > div', t.el);
  const tiempo = $('.tiempo', t.el);
  mic.alTrozo = (pcm, rms, pico) => {
    if (grabando !== i) return;
    trozos.push(pcm);
    muestras += pcm.length;
    // Medidor: -60 dBFS = vacío, 0 dBFS = lleno.
    barra.style.width = `${Math.max(0, Math.min(100, (dbfs(rms) + 60) / 60 * 100))}%`;
    medidor.classList.toggle('satura', pico > 0.98);
    tiempo.textContent = `${(Math.min(muestras, objetivo) / FS).toFixed(1)} s`;
    if (muestras >= objetivo) detener(i);
  };
}

function detener(i) {
  if (grabando !== i) return;
  const t = tomas[i];
  grabando = null;
  mic.alTrozo = null;
  const objetivo = Math.round(DURACION_TOMA_S * FS);
  let pcm = unir(t.trozos);
  if (pcm.length > objetivo) pcm = pcm.slice(0, objetivo);
  delete t.trozos;

  const ev = evaluarToma(pcm);
  if (t.url) URL.revokeObjectURL(t.url);
  t.pcm = pcm;
  t.ok = ev.ok;
  t.url = URL.createObjectURL(wavBlob(pcm));
  const audio = $('audio', t.el);
  audio.src = t.url;
  audio.classList.remove('oculto');
  $('.grabando', t.el).classList.add('oculto');
  pintarToma(i, ev.ok
    ? { estado: '✓ Lista', clase: 'ok', mensaje: 'Escúchala. Si no se entiende bien, repítela.' }
    : { estado: '✗ Repetir', clase: 'mal', mensaje: ev.motivo, claseMensaje: 'mal' });
  actualizarBotones();
}

// ---------- envío ----------

function abrirSocket() {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(urlWs('/ws/registro'));
    ws.binaryType = 'arraybuffer';
    const cola = [];
    const esperando = [];
    ws.recibir = () => (cola.length ? Promise.resolve(cola.shift()) : new Promise((r) => esperando.push(r)));
    ws.onmessage = (e) => {
      const m = JSON.parse(e.data);
      esperando.length ? esperando.shift()(m) : cola.push(m);
    };
    ws.onclose = () => {
      while (esperando.length) esperando.shift()({ tipo: 'error', mensaje: 'Se cortó la conexión con el servidor.' });
    };
    ws.onopen = () => resolve(ws);
    ws.onerror = () => reject(new Error('No se pudo conectar con el servidor.'));
  });
}

async function enviar() {
  enviando = true;
  actualizarBotones();
  mostrarError('');
  const boton = $('#enviar');
  boton.textContent = 'Enviando…';
  let ws;
  try {
    ws = await abrirSocket();
    ws.send(JSON.stringify({
      tipo: 'inicio',
      ...datosAlumno(),
      dispositivo: await infoDispositivo(),
      microfono: mic.info(),
    }));
    let r = await ws.recibir();
    if (r.tipo !== 'listo') throw new Error(r.mensaje);

    let rechazadas = 0;
    for (let i = 0; i < tomas.length; i++) {
      ws.send(JSON.stringify({ tipo: 'toma', numero: i + 1 }));
      const pcm = tomas[i].pcm;
      for (let o = 0; o < pcm.length; o += FS) ws.send(pcm.slice(o, o + FS).buffer); // trozos de 1 s
      ws.send(JSON.stringify({ tipo: 'fin_toma', numero: i + 1 }));
      r = await ws.recibir();
      if (r.tipo === 'error') throw new Error(r.mensaje);
      if (!r.ok) {
        rechazadas++;
        tomas[i].ok = false;
        pintarToma(i, { estado: '✗ Repetir', clase: 'mal', mensaje: `El servidor la rechazó: ${r.motivo}`, claseMensaje: 'mal' });
      }
    }
    if (rechazadas) throw new Error('Repite las tomas marcadas y vuelve a enviar.');

    ws.send(JSON.stringify({ tipo: 'terminar' }));
    r = await ws.recibir();
    if (r.tipo !== 'guardado') throw new Error(r.mensaje);
    mostrarFin(r);
  } catch (e) {
    mostrarError(e.message);
  } finally {
    ws?.close();
    enviando = false;
    boton.textContent = 'Enviar registro';
    actualizarBotones();
  }
}

function mostrarFin(r) {
  const d = datosAlumno();
  mic.cerrar();
  $('#paso-tomas').classList.add('oculto');
  $('#paso-fin').classList.remove('oculto');
  const todoOk = r.verificacion.every((v) => v.ok);
  $('#fin-titulo').textContent = todoOk ? `¡Listo, ${d.nombre.split(' ')[0]}!` : 'Guardado con problemas';
  $('#fin-texto').textContent = (todoOk ? 'Tu voz quedó registrada. ' : 'Avisa al profesor. ')
    + (r.reemplazo_anterior ? 'Reemplazó tu registro anterior.' : '');
  const filas = r.verificacion.map((v) =>
    `<tr><td>${v.archivo}</td><td>${v.frecuencia_hz} Hz</td><td>${v.canales === 1 ? 'mono' : v.canales + ' canales'}</td><td>${v.duracion_s} s</td><td>${v.ok ? '✓' : '✗'}</td></tr>`);
  $('#fin-tabla').innerHTML = filas.join('');
  window.scrollTo(0, 0);
}

// ---------- arranque ----------

async function iniciar() {
  const problema = Microfono.disponible();
  if (problema) mostrarError(problema);
  try {
    config = await (await fetch('/api/registro/config')).json();
  } catch {
    mostrarError('No se pudo conectar con el servidor.');
    return;
  }
  $('#texto-consentimiento').textContent = config.consentimiento;
  crearTomas();
  actualizarBotones();

  for (const id of ['nombre', 'codigo', 'consentimiento']) {
    $(`#${id}`).addEventListener('input', validarDatos);
    $(`#${id}`).addEventListener('change', validarDatos);
  }
  $('#continuar').addEventListener('click', irATomas);
  $('#enviar').addEventListener('click', enviar);
  $('#volver').addEventListener('click', () => {
    $('#paso-tomas').classList.add('oculto');
    $('#paso-datos').classList.remove('oculto');
  });
  $('#otro').addEventListener('click', () => location.reload());
  validarDatos();
}

iniciar();
