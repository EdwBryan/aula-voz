import { Microfono, infoDispositivo, dbfs } from './captura.js';

// Diagnóstico: ¿el celular aplica una compuerta de ruido (audio en cero exacto)?
// Agrupa el audio por segundo y, al detener, manda la serie al servidor (data/pruebas/).
const UMBRAL_CEROS = 0.3;

const $ = (sel) => document.querySelector(sel);
let mic = null;
let inicio = 0;           // cuándo empezó la prueba (ms)
let aperturas = [];       // segundos (desde el inicio) en que se abrió el micrófono
let segundos = [];        // { t, db, ceros, modo }
let acum = { e: 0, ceros: 0, n: 0 };
let primera = null;
let reloj = null;

const modo = () => document.querySelector('input[name="modo"]:checked').value;

function mostrarError(texto) {
  $('#error-general').textContent = texto;
  $('#error-general').classList.toggle('oculto', !texto);
}

function alTrozo(pcm, rms, pico, vaciado, ceros) {
  if (!pcm.length) return;
  acum.e += rms * rms;
  acum.ceros += ceros;
  if (++acum.n < 10) return; // 10 trozos de 100 ms = 1 s
  const t = Math.round((performance.now() - inicio) / 1000);
  const s = { t, db: Math.round(dbfs(Math.sqrt(acum.e / acum.n)) * 10) / 10, ceros: Math.round((acum.ceros / acum.n) * 1000) / 1000, modo: modo() };
  segundos.push(s);
  acum = { e: 0, ceros: 0, n: 0 };
  if (s.ceros > UMBRAL_CEROS && primera === null) primera = t;
  pintar(s);
}

function pintar(s) {
  const ultimos = segundos.slice(-5).map((x) => x.db).sort((a, b) => a - b);
  $('#dato-nivel').textContent = `${s.db.toFixed(0)} dBFS`;
  $('#dato-fondo').textContent = `${ultimos[0].toFixed(0)} dBFS`;
  $('#dato-ceros').textContent = `${Math.round(s.ceros * 100)} %`;
  $('#dato-primera').textContent = primera === null ? 'no' : `a los ${primera} s`;

  const activo = s.ceros > UMBRAL_CEROS;
  $('#indicador').className = `indicador ${activo ? 'grabando' : 'fin'}`;
  $('#indicador-titulo').textContent = activo ? '⚠ Filtro activo' : '✓ Audio limpio';
  $('#indicador-texto').textContent = activo
    ? 'El celular está silenciando el sonido de fondo.'
    : 'Se oye el ambiente completo, sin cortes.';
  dibujar();
}

function dibujar() {
  const c = $('#grafico');
  const w = (c.width = c.clientWidth * devicePixelRatio);
  const h = (c.height = 160 * devicePixelRatio);
  const g = c.getContext('2d');
  g.clearRect(0, 0, w, h);
  const vista = segundos.slice(-180);
  const ancho = w / 180;
  vista.forEach((s, i) => {
    const alto = Math.max(1, ((s.db + 90) / 90) * h);
    g.fillStyle = s.ceros > UMBRAL_CEROS ? '#cf222e' : '#1a7f37';
    g.fillRect(i * ancho, h - alto, Math.max(1, ancho - 1), alto);
  });
  // Marca dónde se reabrió el micrófono.
  g.fillStyle = '#1f6feb';
  for (const a of aperturas.slice(1)) {
    const i = vista.findIndex((s) => s.t >= a);
    if (i >= 0) g.fillRect(i * ancho, 0, 2, h);
  }
}

async function abrir() {
  mic = new Microfono();
  await mic.abrir({ filtros: modo() === 'con' });
  mic.alTrozo = alTrozo;
  aperturas.push(Math.round((performance.now() - inicio) / 1000));
  const i = mic.info();
  $('#dato-ajustes').textContent = `Ajustes aplicados: eco ${i.echoCancellation}, ruido ${i.noiseSuppression}, ganancia ${i.autoGainControl}, ${i.frecuencia_nativa_hz} Hz.`;
}

async function empezar() {
  mostrarError('');
  segundos = []; aperturas = []; primera = null; acum = { e: 0, ceros: 0, n: 0 };
  inicio = performance.now();
  try {
    await abrir();
  } catch (e) {
    mostrarError(`No se pudo abrir el micrófono: ${e.message}`);
    return;
  }
  try { await navigator.wakeLock?.request('screen'); } catch { /* opcional */ }
  $('#empezar').classList.add('oculto');
  $('#reabrir').classList.remove('oculto');
  $('#detener').classList.remove('oculto');
  reloj = setInterval(() => {
    const s = Math.floor((performance.now() - inicio) / 1000);
    $('#dato-tiempo').textContent = `${Math.floor(s / 60)} min ${s % 60} s`;
  }, 500);
  $('#indicador-titulo').textContent = 'Midiendo…';
  $('#indicador-texto').textContent = '';
}

async function reabrir() {
  await mic.cerrar();
  acum = { e: 0, ceros: 0, n: 0 };
  await abrir();
}

async function detener() {
  clearInterval(reloj);
  const info = mic.info();
  await mic.cerrar();
  $('#detener').disabled = true;
  try {
    const r = await fetch('/api/prueba-mic', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ dispositivo: await infoDispositivo(), microfono: info, aperturas, primera_compuerta_s: primera, segundos }),
    });
    const d = await r.json();
    $('#indicador-texto').textContent = `Resultado enviado (${d.archivo}).`;
  } catch {
    mostrarError('No se pudo enviar el resultado al servidor.');
  }
  $('#reabrir').classList.add('oculto');
  $('#detener').classList.add('oculto');
  $('#detener').disabled = false;
  $('#empezar').classList.remove('oculto');
}

const problema = Microfono.disponible();
if (problema) mostrarError(problema);
$('#empezar').addEventListener('click', empezar);
$('#reabrir').addEventListener('click', reabrir);
$('#detener').addEventListener('click', detener);
