// AudioWorklet: convierte el micrófono a mono, 16 kHz, PCM de 16 bits.
//
// El AudioContext corre a la frecuencia nativa del celular (casi siempre 48 kHz,
// a veces 44.1 kHz). Aquí se baja a 16 kHz con un filtro sinc con ventana de
// Blackman (pasa-bajos ~7.3 kHz) para que no haya aliasing: no basta con tomar
// una de cada tres muestras.
//
// Envía al hilo principal mensajes { pcm: Int16Array, rms, pico, ceros } cada ~100 ms.
// 'ceros' es la fracción de muestras de ENTRADA que llegaron en cero exacto: un micrófono
// real siempre tiene algo de ruido, así que muchos ceros = el celular está cortando el audio.

const FS_SALIDA = 16000;
const CORTE_HZ = 7300;          // frecuencia de corte del pasa-bajos
const LADOS_POR_RAZON = 32;     // medio ancho del filtro = 32 * (fs_entrada / fs_salida) muestras
const RESOLUCION_TABLA = 256;   // puntos de la tabla del filtro por muestra de entrada
const MUESTRAS_POR_TROZO = 1600; // 100 ms a 16 kHz

class ConvertidorPCM extends AudioWorkletProcessor {
  constructor() {
    super();
    const fsEntrada = sampleRate; // global del AudioWorkletGlobalScope
    this.pasoBase = fsEntrada / FS_SALIDA;
    this.paso = this.pasoBase; // {ritmo} lo ajusta si el reloj de audio del celular no va exacto
    this.medio = Math.ceil(LADOS_POR_RAZON * this.paso);
    this.fc = CORTE_HZ / fsEntrada; // corte normalizado (ciclos por muestra de entrada)
    this.tabla = this.crearTabla();

    // Historial de entrada: empieza con ceros para poder centrar el filtro en t=0.
    this.buf = new Float32Array(this.medio * 2 + 4096);
    this.largo = this.medio;
    this.t = this.medio; // posición (en muestras de entrada) de la próxima salida

    this.salida = new Int16Array(MUESTRAS_POR_TROZO);
    this.n = 0;
    this.suma2 = 0;
    this.pico = 0;
    this.cerosEntrada = 0;
    this.totalEntrada = 0;
    this.frameAnterior = null;
    this.framesSaltados = 0; // acumulado: muestras de entrada que el navegador no entregó

    // 'vaciar': manda ya el trozo incompleto (al detener la grabación). Siempre responde
    // con un mensaje marcado vaciado=true, aunque no quede nada, para saber que terminó.
    this.port.onmessage = (e) => {
      if (e.data?.ritmo) {
        // ritmo > 1: entregar más muestras por muestra de entrada (reloj de audio lento).
        this.paso = this.pasoBase / e.data.ritmo;
        return;
      }
      if (e.data !== 'vaciar') return;
      if (this.n > 0) this.enviarTrozo();
      else this.port.postMessage({ pcm: new Int16Array(0), rms: 0, pico: 0, ceros: 0, vaciado: true });
    };
  }

  crearTabla() {
    const n = this.medio * RESOLUCION_TABLA;
    const tabla = new Float32Array(n + 2);
    for (let i = 0; i <= n + 1; i++) {
      const d = i / RESOLUCION_TABLA; // distancia en muestras de entrada
      const x = 2 * this.fc * d;
      const sinc = d === 0 ? 1 : Math.sin(Math.PI * x) / (Math.PI * x);
      const w = d >= this.medio ? 0
        : 0.42 + 0.5 * Math.cos(Math.PI * d / this.medio) + 0.08 * Math.cos(2 * Math.PI * d / this.medio);
      tabla[i] = 2 * this.fc * sinc * w;
    }
    return tabla;
  }

  nucleo(d) {
    d = Math.abs(d) * RESOLUCION_TABLA;
    const i = d | 0;
    const f = d - i;
    return this.tabla[i] + (this.tabla[i + 1] - this.tabla[i]) * f;
  }

  agregar(mono) {
    if (this.largo + mono.length > this.buf.length) {
      const nuevo = new Float32Array((this.largo + mono.length) * 2);
      nuevo.set(this.buf.subarray(0, this.largo));
      this.buf = nuevo;
    }
    this.buf.set(mono, this.largo);
    this.largo += mono.length;
  }

  emitir(x) {
    const v = Math.max(-1, Math.min(1, x));
    const s = Math.round(v < 0 ? v * 32768 : v * 32767);
    this.salida[this.n++] = s;
    this.suma2 += v * v;
    const a = Math.abs(v);
    if (a > this.pico) this.pico = a;
    if (this.n === MUESTRAS_POR_TROZO) this.enviarTrozo();
  }

  enviarTrozo() {
    const pcm = this.n === MUESTRAS_POR_TROZO ? this.salida : this.salida.slice(0, this.n);
    this.port.postMessage(
      {
        pcm,
        rms: Math.sqrt(this.suma2 / this.n),
        pico: this.pico,
        ceros: this.totalEntrada ? this.cerosEntrada / this.totalEntrada : 0,
        saltados: this.framesSaltados,
        vaciado: this.n < MUESTRAS_POR_TROZO,
      },
      [pcm.buffer],
    );
    this.salida = new Int16Array(MUESTRAS_POR_TROZO);
    this.n = 0;
    this.suma2 = 0;
    this.pico = 0;
    this.cerosEntrada = 0;
    this.totalEntrada = 0;
  }

  process(entradas) {
    if (this.frameAnterior !== null && currentFrame - this.frameAnterior > 128) {
      this.framesSaltados += currentFrame - this.frameAnterior - 128;
    }
    this.frameAnterior = currentFrame;

    const entrada = entradas[0];
    if (!entrada || entrada.length === 0) return true;

    // Mezcla a mono (promedio de canales).
    const len = entrada[0].length;
    let mono = entrada[0];
    if (entrada.length > 1) {
      mono = new Float32Array(len);
      for (const canal of entrada) for (let i = 0; i < len; i++) mono[i] += canal[i];
      for (let i = 0; i < len; i++) mono[i] /= entrada.length;
    }
    for (let i = 0; i < len; i++) if (mono[i] === 0) this.cerosEntrada++;
    this.totalEntrada += len;
    this.agregar(mono);

    // Calcula todas las salidas cuyo filtro ya tiene datos completos a la derecha.
    const buf = this.buf;
    const medio = this.medio;
    while (this.t + medio < this.largo) {
      const centro = this.t;
      const ini = Math.ceil(centro - medio);
      const fin = Math.floor(centro + medio);
      let acc = 0;
      for (let k = ini; k <= fin; k++) acc += buf[k] * this.nucleo(centro - k);
      this.emitir(acc);
      this.t += this.paso;
    }

    // Descarta historial que ya no se necesita.
    const descartar = Math.floor(this.t) - medio - 1;
    if (descartar > 0) {
      buf.copyWithin(0, descartar, this.largo);
      this.largo -= descartar;
      this.t -= descartar;
    }
    return true;
  }
}

registerProcessor('convertidor-pcm', ConvertidorPCM);
