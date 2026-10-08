// Captura de micrófono compartida por el modo registro y el modo clase.
// Abre el micrófono SIN procesamiento (sin cancelación de eco, sin supresión de
// ruido, sin control automático de ganancia) y entrega trozos Int16 a 16 kHz mono.

export const FS = 16000;

export class Microfono {
  constructor() {
    this.ctx = null;
    this.stream = null;
    this.nodo = null;
    this.alTrozo = null; // función (pcm: Int16Array, rms, pico, vaciado: bool, ceros: 0..1)
    this.saltados = 0;   // muestras de entrada que el navegador se saltó (acumulado)
  }

  static disponible() {
    if (!window.isSecureContext) {
      return 'La página no está en modo seguro. Ábrela con https:// (no http://).';
    }
    if (!navigator.mediaDevices?.getUserMedia) {
      return 'Este navegador no permite usar el micrófono. Usa Chrome o Safari actualizados.';
    }
    if (!window.AudioWorkletNode) {
      return 'Este navegador no soporta AudioWorklet. Actualiza Chrome o Safari.';
    }
    return null;
  }

  // filtros=true deja que el navegador aplique eco/ruido/ganancia (solo para diagnóstico).
  async abrir({ filtros = false } = {}) {
    if (this.ctx) return;
    // Se crea antes del primer await: Safari solo deja arrancar el audio dentro del toque.
    // Frecuencia nativa: el remuestreo a 16 kHz lo hace el worklet con su propio filtro.
    this.ctx = new AudioContext({ latencyHint: 'interactive' });
    try {
      this.stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: filtros,
          noiseSuppression: filtros,
          autoGainControl: filtros,
          channelCount: 1,
        },
        video: false,
      });
    } catch (e) {
      await this.ctx.close();
      this.ctx = null;
      throw e;
    }
    await this.ctx.audioWorklet.addModule('/static/js/pcm-worklet.js');
    const fuente = this.ctx.createMediaStreamSource(this.stream);
    this.nodo = new AudioWorkletNode(this.ctx, 'convertidor-pcm', {
      numberOfInputs: 1,
      numberOfOutputs: 1,
      outputChannelCount: [1],
      channelCount: 1,
      channelCountMode: 'explicit',
      channelInterpretation: 'speakers', // mezcla a mono si llegara estéreo
    });
    this.nodo.port.onmessage = (e) => {
      if (e.data.saltados !== undefined) this.saltados = e.data.saltados;
      if (this.alTrozo) this.alTrozo(e.data.pcm, e.data.rms, e.data.pico, !!e.data.vaciado, e.data.ceros ?? 0);
    };
    // Conectado a un destino silenciado: algunos navegadores no procesan nodos sueltos.
    const silencio = this.ctx.createGain();
    silencio.gain.value = 0;
    fuente.connect(this.nodo).connect(silencio).connect(this.ctx.destination);
    if (this.ctx.state !== 'running') await this.ctx.resume();
    // Punto de partida para medir el ritmo real del reloj de audio (tras el arranque).
    setTimeout(() => { if (this.ctx) this.relojBase = this.marcaReloj(); }, 1000);
  }

  marcaReloj() {
    const ts = this.ctx.getOutputTimestamp?.();
    if (ts && ts.contextTime > 0 && ts.performanceTime > 0) return { ctx: ts.contextTime, perf: ts.performanceTime };
    return { ctx: this.ctx.currentTime, perf: performance.now() };
  }

  // Ritmo del reloj de audio frente al reloj del sistema: 1 = exacto, 0.989 = 1.1 % lento.
  // Algunos celulares (un Vivo probado) entregan ~1 % menos muestras de las que dicen.
  ritmoReloj() {
    if (!this.ctx || !this.relojBase) return null;
    const m = this.marcaReloj();
    const dt = (m.perf - this.relojBase.perf) / 1000;
    return dt >= 5 ? (m.ctx - this.relojBase.ctx) / dt : null;
  }

  ajustarRitmo(ritmo) {
    this.nodo?.port.postMessage({ ritmo });
  }

  vaciar() {
    this.nodo?.port.postMessage('vaciar');
  }

  // Lo que el navegador realmente aplicó (puede ignorar lo pedido).
  info() {
    const pista = this.stream?.getAudioTracks()[0];
    const s = pista?.getSettings?.() ?? {};
    return {
      etiqueta: pista?.label ?? null,
      frecuencia_nativa_hz: this.ctx?.sampleRate ?? null,
      echoCancellation: s.echoCancellation ?? null,
      noiseSuppression: s.noiseSuppression ?? null,
      autoGainControl: s.autoGainControl ?? null,
      channelCount: s.channelCount ?? null,
      sampleRate: s.sampleRate ?? null,
    };
  }

  async cerrar() {
    this.stream?.getTracks().forEach((t) => t.stop());
    if (this.ctx) await this.ctx.close();
    this.ctx = this.stream = this.nodo = this.relojBase = null;
  }
}

// Datos del celular (modelo solo si el navegador lo da: Chrome en Android sí).
export async function infoDispositivo() {
  const info = { user_agent: navigator.userAgent, modelo: null, plataforma: null, version_plataforma: null };
  try {
    const ua = navigator.userAgentData;
    if (ua?.getHighEntropyValues) {
      const v = await ua.getHighEntropyValues(['model', 'platform', 'platformVersion']);
      info.modelo = v.model || null;
      info.plataforma = v.platform || null;
      info.version_plataforma = v.platformVersion || null;
    }
  } catch { /* el navegador no lo comparte */ }
  if (!info.modelo) {
    // Respaldo: Android suele poner el modelo en el user agent ("...; SM-A515F) ...").
    const m = navigator.userAgent.match(/Android [\d.]+; ([^;)]+)\)/);
    if (m && m[1] !== 'K') info.modelo = m[1].trim();
  }
  return info;
}

// Nivel en dBFS (para mostrar y para validar).
export function dbfs(x) {
  return x > 0 ? 20 * Math.log10(x) : -120;
}

// WAV de 16 bits mono para escuchar la toma en el celular.
export function wavBlob(pcm, fs = FS) {
  const buf = new ArrayBuffer(44 + pcm.length * 2);
  const v = new DataView(buf);
  const txt = (o, s) => { for (let i = 0; i < s.length; i++) v.setUint8(o + i, s.charCodeAt(i)); };
  txt(0, 'RIFF'); v.setUint32(4, 36 + pcm.length * 2, true); txt(8, 'WAVE');
  txt(12, 'fmt '); v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true);
  v.setUint32(24, fs, true); v.setUint32(28, fs * 2, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true);
  txt(36, 'data'); v.setUint32(40, pcm.length * 2, true);
  new Int16Array(buf, 44).set(pcm);
  return new Blob([buf], { type: 'audio/wav' });
}

export function unir(trozos) {
  const total = trozos.reduce((a, t) => a + t.length, 0);
  const out = new Int16Array(total);
  let o = 0;
  for (const t of trozos) { out.set(t, o); o += t.length; }
  return out;
}

export function urlWs(ruta) {
  return `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}${ruta}`;
}
