import { FS, Microfono, infoDispositivo, dbfs, urlWs } from './captura.js';

// Protocolo con el servidor: ver server/main.py (ws_clase) y server/clase.py.
const SUGERENCIAS = ['mic-1', 'mic-2', 'mic-3', 'mic-4', 'profe'];
const CABECERA = 12; // tramo, seq, muestra (uint32 LE)

const $ = (sel) => document.querySelector(sel);
const ahora = () => performance.timeOrigin + performance.now();

const mic = new Microfono();
let ws = null;
let activo = false;        // unido a la clase (o intentando)
let conectado = false;     // el servidor aceptó el "unirse"
let reintento = 0;
let nombre = null;
let info = null;
let sesion = null;         // { curso, tema, estado }
let grabacion = null;      // tramo en curso: { id, motivo, muestra, seq, tInicio, cerrando }
let iniciarPendiente = null;
let ritmo = 1; // corrección del reloj de audio del celular (ver corregirRitmo)
let grabadoMuestras = 0;
let bateria = null;
let wakeLock = null;
let pingTimer = null;
const reloj = { muestras: [], desfase: 0, rtt: null };
let nivelAcum = { rms: 0, pico: 0, ceros: 0, n: 0 };
let cerosRecientes = 0; // promedio móvil (~3 s) de muestras en cero exacto
let avisoCompuerta = false;

let clienteId;
try {
  clienteId = localStorage.getItem('aula-cliente') || crypto.randomUUID();
  localStorage.setItem('aula-cliente', clienteId);
} catch {
  clienteId = crypto.randomUUID();
}

// ---------- utilidades ----------

function enviar(msg) {
  if (ws?.readyState === WebSocket.OPEN) ws.send(JSON.stringify(msg));
}

function mostrarError(texto) {
  const el = $('#error-general');
  el.textContent = texto;
  el.classList.toggle('oculto', !texto);
}

function hms(s) {
  s = Math.floor(s);
  const p = (n) => String(n).padStart(2, '0');
  return `${p(Math.floor(s / 3600))}:${p(Math.floor(s / 60) % 60)}:${p(s % 60)}`;
}

// ---------- pantalla ----------

function pintar() {
  const ind = $('#indicador');
  let clase = 'espera', titulo = 'Esperando', texto = '';
  const estado = sesion?.estado;
  if (!conectado) {
    clase = 'espera'; titulo = 'Sin conexión'; texto = 'Reconectando con la laptop…';
  } else if (!sesion) {
    titulo = 'Esperando'; texto = 'El profesor todavía no creó la clase.';
  } else if (grabacion && !grabacion.cerrando) {
    clase = 'grabando'; titulo = '● GRABANDO'; texto = 'Deja el celular quieto y con la pantalla encendida.';
  } else if (estado === 'pausada') {
    clase = 'pausa'; titulo = 'En pausa'; texto = 'Descanso: no se está grabando.';
  } else if (estado === 'terminando' || estado === 'terminada') {
    clase = 'fin'; titulo = 'Clase terminada'; texto = 'Gracias. Ya puedes cerrar esta página.';
  } else {
    titulo = 'Listo'; texto = 'Esperando que el profesor inicie la grabación.';
  }
  ind.className = `indicador ${clase}`;
  $('#indicador-titulo').textContent = titulo;
  $('#indicador-texto').textContent = texto;

  $('#dato-nombre').textContent = nombre ?? '';
  $('#dato-sesion').textContent = sesion ? [sesion.curso, sesion.tema].filter(Boolean).join(' — ') : '—';
  $('#dato-conexion').textContent = conectado ? 'Conectado' : 'Reconectando…';
  $('#dato-conexion').className = conectado ? 'ok' : 'mal';
  $('#tiempo').textContent = hms(grabadoMuestras / FS);
  $('#cambiar').classList.toggle('oculto', !!grabacion);

  const avisos = [];
  if (avisoCompuerta) {
    avisos.push('⚠ El celular está silenciando el audio (filtro de ruido del sistema). Revisa la página /prueba-mic.');
  }
  if (bateria && bateria.nivel <= 20 && !bateria.cargando) {
    avisos.push(`🔋 Batería baja (${bateria.nivel} %). Conecta el cargador.`);
  }
  if (!('wakeLock' in navigator)) {
    avisos.push('Este navegador no puede mantener la pantalla encendida: súbele el tiempo de apagado de pantalla en Ajustes.');
  } else if (!wakeLock && activo) {
    avisos.push('Toca la pantalla para mantenerla encendida.');
  }
  $('#avisos').innerHTML = avisos.map((a) => `<div class="aviso">${a}</div>`).join('');
}

// ---------- conexión ----------

function conectar() {
  ws = new WebSocket(urlWs('/ws/clase'));
  ws.binaryType = 'arraybuffer';
  ws.onopen = () => {
    reintento = 0;
    enviar({
      tipo: 'unirse',
      dispositivo: nombre,
      cliente_id: clienteId,
      info,
      microfono: mic.info(),
      bateria,
      tramo_actual: grabacion && !grabacion.cerrando ? grabacion.id : null,
    });
    // Varias mediciones rápidas del reloj al conectar, luego una cada 10 s.
    for (let i = 0; i < 5; i++) setTimeout(() => enviar({ tipo: 'ping', t: ahora() }), i * 200);
    clearInterval(pingTimer);
    pingTimer = setInterval(() => enviar({ tipo: 'ping', t: ahora() }), 10000);
  };
  ws.onmessage = (e) => manejar(JSON.parse(e.data));
  ws.onclose = () => {
    conectado = false;
    clearInterval(pingTimer);
    pintar();
    if (activo) setTimeout(conectar, Math.min(1000 * 2 ** reintento++, 5000));
  };
}

function manejar(m) {
  switch (m.tipo) {
    case 'unido':
      conectado = true;
      mostrarError('');
      sesion = m.sesion;
      // Si veníamos grabando, el servidor necesita saber de nuevo cuál es el tramo.
      if (grabacion?.tInicio != null) enviarTramo(grabacion);
      break;
    case 'error':
      if (!conectado) { // nombre rechazado
        activo = false;
        ws.close();
        volverANombre();
      }
      mostrarError(m.mensaje);
      break;
    case 'pong': {
      const t = ahora();
      const rtt = t - m.t_cliente;
      reloj.muestras.push({ rtt, desfase: m.t_servidor - (m.t_cliente + t) / 2 });
      if (reloj.muestras.length > 20) reloj.muestras.shift();
      const mejor = reloj.muestras.reduce((a, b) => (b.rtt < a.rtt ? b : a));
      reloj.desfase = mejor.desfase;
      reloj.rtt = mejor.rtt;
      enviar({ tipo: 'reloj', desfase_ms: Math.round(mejor.desfase), rtt_ms: Math.round(mejor.rtt) });
      break;
    }
    case 'sesion':
      if (m.sesion?.estado === 'preparada' && sesion?.estado !== 'preparada') grabadoMuestras = 0; // clase nueva
      sesion = m.sesion;
      break;
    case 'iniciar':
      if (grabacion?.cerrando) iniciarPendiente = m.motivo;
      else if (!grabacion) empezarTramo(m.motivo);
      break;
    case 'pausar':
    case 'terminar':
      iniciarPendiente = null;
      cerrarTramo();
      break;
    case 'marca':
      if (grabacion) enviar({ tipo: 'marca_ok', n: m.n, tramo: grabacion.id, muestra: grabacion.muestra });
      destello(`Marca ${m.n}`);
      break;
  }
  pintar();
}

// ---------- grabación ----------

// El reloj de audio de algunos celulares no va exacto (el Vivo probado va 1.06 % lento):
// sin corregir, en 2 h el WAV quedaría ~76 s más corto que la clase. Se ajusta el ritmo del
// conversor para entregar 16 000 muestras por segundo REAL, y el pequeño atraso acumulado
// se recupera de a poco (en ~30 s, como máximo 0.2 % más rápido), sin saltos.
function corregirRitmo(g) {
  const k = mic.ritmoReloj();
  if (!k || g.tInicioPerf == null) return;
  const atraso = g.muestra / FS - (performance.now() - g.tInicioPerf) / 1000; // s; negativo = atrasado
  const recuperar = Math.max(-0.002, Math.min(0.002, -atraso / 30));
  ritmo = Math.max(0.97, Math.min(1.03, (1 / k) * (1 + recuperar)));
  mic.ajustarRitmo(ritmo);
}

function empezarTramo(motivo) {
  const k = mic.ritmoReloj();
  if (k) {
    ritmo = Math.max(0.97, Math.min(1.03, 1 / k));
    mic.ajustarRitmo(ritmo);
  }
  grabacion = {
    id: crypto.getRandomValues(new Uint32Array(1))[0],
    motivo,
    muestra: 0,
    seq: 0,
    tInicio: null, // hora del servidor de la primera muestra; se fija con el primer trozo
    cerrando: false,
  };
}

function cerrarTramo() {
  if (!grabacion || grabacion.cerrando) return;
  grabacion.cerrando = true;
  mic.vaciar(); // el worklet responde con el último trozo marcado como vaciado
}

function enviarTramo(g) {
  enviar({ tipo: 'tramo', tramo: g.id, motivo: g.motivo, t_inicio_ms: g.tInicio });
}

function enviarTrozo(g, pcm) {
  const buf = new ArrayBuffer(CABECERA + pcm.length * 2);
  const v = new DataView(buf);
  v.setUint32(0, g.id, true);
  v.setUint32(4, g.seq, true);
  v.setUint32(8, g.muestra, true);
  new Int16Array(buf, CABECERA).set(pcm);
  // Sin conexión, por ahora el trozo se pierde y queda anotado como hueco en el servidor.
  if (ws?.readyState === WebSocket.OPEN && conectado) ws.send(buf);
}

function alTrozo(pcm, rms, pico, vaciado, ceros) {
  // Nivel para el medidor de aquí y para el panel (5 veces por segundo).
  nivelAcum.rms = Math.max(nivelAcum.rms, rms);
  nivelAcum.pico = Math.max(nivelAcum.pico, pico);
  nivelAcum.ceros += ceros;
  cerosRecientes += (ceros - cerosRecientes) / 30;
  if (++nivelAcum.n >= 2) {
    const db = dbfs(nivelAcum.rms);
    $('.medidor > div').style.width = `${Math.max(0, Math.min(100, (db + 60) / 60 * 100))}%`;
    $('.medidor').classList.toggle('satura', nivelAcum.pico > 0.98);
    enviar({ tipo: 'nivel', db, pico: nivelAcum.pico, ceros: nivelAcum.ceros / nivelAcum.n, pendientes_s: 0 });
    nivelAcum = { rms: 0, pico: 0, ceros: 0, n: 0 };
    const compuerta = cerosRecientes > 0.3;
    if (compuerta !== avisoCompuerta) { avisoCompuerta = compuerta; pintar(); }
  }

  const g = grabacion;
  if (!g) return;
  if (pcm.length) {
    if (g.tInicio === null) {
      // La primera muestra de este trozo se grabó hace (largo del trozo) ms.
      g.tInicioPerf = performance.now() - (pcm.length * 1000) / FS;
      g.tInicio = g.tInicioPerf + performance.timeOrigin + reloj.desfase;
      enviarTramo(g);
    }
    enviarTrozo(g, pcm);
    g.muestra += pcm.length;
    g.seq++;
    if (g.seq % 20 === 0) corregirRitmo(g); // cada 2 s
    if (g.seq % 100 === 0) { // cada 10 s: para medir si el reloj de audio se atrasa
      enviar({
        tipo: 'deriva', tramo: g.id, muestra: g.muestra, t_ms: ahora() + reloj.desfase,
        ctx_s: mic.ctx?.currentTime ?? null, saltados: mic.saltados, ritmo,
      });
    }
    grabadoMuestras += pcm.length;
    $('#tiempo').textContent = hms(grabadoMuestras / FS);
  }
  if (vaciado && g.cerrando) {
    if (g.tInicio !== null) {
      enviar({ tipo: 'fin_tramo', tramo: g.id, muestras: g.muestra, seq_final: g.seq - 1, t_fin_ms: ahora() + reloj.desfase });
    }
    grabacion = null;
    if (iniciarPendiente) {
      empezarTramo(iniciarPendiente);
      iniciarPendiente = null;
    }
    pintar();
  }
}

function destello(texto) {
  const ind = $('#indicador');
  ind.classList.add('destello');
  $('#indicador-texto').textContent = texto;
  navigator.vibrate?.(200);
  setTimeout(() => { ind.classList.remove('destello'); pintar(); }, 1200);
}

// ---------- pantalla encendida y batería ----------

async function pedirWakeLock() {
  if (!('wakeLock' in navigator) || wakeLock || document.visibilityState !== 'visible') return;
  try {
    wakeLock = await navigator.wakeLock.request('screen');
    wakeLock.addEventListener('release', () => { wakeLock = null; pintar(); });
  } catch { /* se reintenta al tocar la pantalla o al volver a la página */ }
  pintar();
}

async function vigilarBateria() {
  if (!navigator.getBattery) return; // Safari y Firefox no lo dan
  try {
    const b = await navigator.getBattery();
    const actualizar = () => {
      bateria = { nivel: Math.round(b.level * 100), cargando: b.charging };
      enviar({ tipo: 'bateria', bateria });
      pintar();
    };
    b.addEventListener('levelchange', actualizar);
    b.addEventListener('chargingchange', actualizar);
    actualizar();
  } catch { /* sin datos de batería */ }
}

// ---------- pasos ----------

function volverANombre() {
  $('#paso-grabar').classList.add('oculto');
  $('#paso-nombre').classList.remove('oculto');
}

async function unirme() {
  nombre = $('#nombre').value.trim().toLowerCase();
  mostrarError('');
  try {
    localStorage.setItem('aula-nombre', nombre);
  } catch { /* sin almacenamiento */ }
  try {
    await mic.abrir();
  } catch (e) {
    mostrarError(e.name === 'NotAllowedError'
      ? 'No diste permiso para usar el micrófono. Actívalo en los permisos del sitio y vuelve a intentar.'
      : `No se pudo abrir el micrófono: ${e.message}`);
    return;
  }
  mic.alTrozo = alTrozo;
  info = await infoDispositivo();
  await pedirWakeLock();
  $('#paso-nombre').classList.add('oculto');
  $('#paso-grabar').classList.remove('oculto');
  activo = true;
  conectar();
  pintar();
}

function iniciar() {
  const problema = Microfono.disponible();
  if (problema) mostrarError(problema);

  const cont = $('#sugerencias');
  for (const s of SUGERENCIAS) {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'chip';
    b.textContent = s;
    b.addEventListener('click', () => { $('#nombre').value = s; validar(); });
    cont.appendChild(b);
  }
  const validar = () => {
    $('#unirme').disabled = !/^[a-z0-9][a-z0-9-]{0,19}$/.test($('#nombre').value.trim().toLowerCase());
  };
  try {
    $('#nombre').value = localStorage.getItem('aula-nombre') || '';
  } catch { /* sin almacenamiento */ }
  $('#nombre').addEventListener('input', validar);
  $('#unirme').addEventListener('click', unirme);
  $('#cambiar').addEventListener('click', () => {
    activo = false;
    conectado = false;
    ws?.close();
    volverANombre();
  });
  validar();

  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible' && activo) pedirWakeLock();
  });
  document.addEventListener('click', () => { if (activo) pedirWakeLock(); });
  window.addEventListener('beforeunload', (e) => {
    if (grabacion) e.preventDefault(); // pide confirmación si está grabando
  });
  vigilarBateria();
}

iniciar();
