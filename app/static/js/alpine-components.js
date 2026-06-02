/**
 * Componentes Alpine registrados globalmente (Agents.md §5).
 * Debe cargarse antes de alpine.min.js para capturar alpine:init.
 */
function registerInvoicesTableColumns() {
  const STORAGE_KEY = "invoicesTableColWidths";
  const DEFAULT_WIDTHS = {
    fecha: 96,
    doc_type: 128,
    proveedor: 200,
    cif_nif: 104,
    total: 96,
    created_at: 128,
    status: 88,
    actions: 72,
  };
  const MIN_COL_WIDTH = 48;

  Alpine.data("invoicesTableColumns", () => ({
    widths: { ...DEFAULT_WIDTHS },
    resizing: null,

    init() {
      try {
        const raw = localStorage.getItem(STORAGE_KEY);
        if (raw) {
          const saved = JSON.parse(raw);
          this.widths = { ...DEFAULT_WIDTHS, ...saved };
        }
      } catch {
        this.widths = { ...DEFAULT_WIDTHS };
      }
    },

    colStyle(key) {
      const w = this.widths[key] ?? DEFAULT_WIDTHS[key] ?? MIN_COL_WIDTH;
      return `width: ${w}px; min-width: ${MIN_COL_WIDTH}px; max-width: ${w}px;`;
    },

    tableStyle() {
      const sum = Object.values(this.widths).reduce((acc, n) => acc + Number(n), 0);
      return `width: ${Math.max(sum, 640)}px; table-layout: fixed;`;
    },

    onResizeStart(key, event) {
      event.preventDefault();
      event.stopPropagation();
      this.resizing = {
        key,
        startX: event.clientX,
        startW: this.widths[key] ?? DEFAULT_WIDTHS[key] ?? MIN_COL_WIDTH,
      };
      document.body.classList.add("invoices-col-resizing");
      const handle = event.currentTarget;
      if (handle instanceof HTMLElement && handle.setPointerCapture) {
        try {
          handle.setPointerCapture(event.pointerId);
        } catch {
          /* ignore */
        }
      }
    },

    onResizeMove(event) {
      if (!this.resizing) return;
      const delta = event.clientX - this.resizing.startX;
      const next = Math.max(MIN_COL_WIDTH, this.resizing.startW + delta);
      this.widths = { ...this.widths, [this.resizing.key]: next };
    },

    onResizeEnd() {
      if (!this.resizing) return;
      try {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(this.widths));
      } catch {
        /* localStorage no disponible */
      }
      this.resizing = null;
      document.body.classList.remove("invoices-col-resizing");
    },
  }));
}

/** Auto-scroll del hilo de chat mientras llegan chunks SSE o nuevos mensajes HTMX. */
function registerChatMessagesScroll() {
  Alpine.data("chatMessagesScroll", () => ({
    _observer: null,

    init() {
      this.scrollBottom();
      this._observer = new MutationObserver(() => this.scrollBottom());
      this._observer.observe(this.$el, {
        childList: true,
        subtree: true,
        characterData: true,
      });
    },

    scrollBottom() {
      const el = this.$el;
      requestAnimationFrame(() => {
        requestAnimationFrame(() => {
          el.scrollTo({ top: el.scrollHeight, behavior: "instant" });
        });
      });
    },

    destroy() {
      this._observer?.disconnect();
    },
  }));
}

/** HTMX inserta HTML sin procesar; Alpine debe inicializar cada swap (chat, modales, etc.). */
function registerHtmxAlpineBridge() {
  const initAlpineOnSwap = (event) => {
    const target = event.detail?.target;
    if (!(target instanceof Element)) return;
    if (window.htmx?.process) {
      window.htmx.process(target);
    }
    if (window.Alpine) {
      window.Alpine.initTree(target);
    }
  };
  document.addEventListener("htmx:afterSwap", initAlpineOnSwap);
  document.addEventListener("htmx:afterSettle", initAlpineOnSwap);
}

/** Limpia el textarea del compositor tras POST HTMX exitoso (sin depender solo de Alpine). */
function registerChatComposerClear() {
  window.chatComposerClear = (event) => {
    if (!event?.detail?.successful) return;
    const form =
      event.detail.elt instanceof HTMLFormElement
        ? event.detail.elt
        : document.getElementById("chat-composer");
    if (!(form instanceof HTMLFormElement)) return;
    const textarea = form.querySelector("#chat-content");
    if (textarea instanceof HTMLTextAreaElement) {
      textarea.value = "";
      textarea.dispatchEvent(new Event("input", { bubbles: true }));
    }
    if (window.Alpine) {
      const data = window.Alpine.$data(form);
      if (data && Object.prototype.hasOwnProperty.call(data, "content")) {
        data.content = "";
      }
    }
    document.dispatchEvent(new CustomEvent("chat-scroll-bottom", { bubbles: true }));
  };
}

/** Panel de eventos del calendario con estado de grabación de voz. */
function registerCalendarEventsPanel() {
  Alpine.data("calendarEventsPanel", () => ({
    createOpen: false,
    micState: "idle", // idle | recording | unsupported
    micError: "",
    _recognition: null,

    startVoice() {
      const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
      if (!SR) {
        this.micError = "Tu navegador no soporta reconocimiento de voz (prueba Chrome o Edge).";
        this.micState = "unsupported";
        return;
      }
      if (this.micState === "recording") {
        this._recognition?.stop();
        return;
      }
      const recognition = new SR();
      recognition.lang = "es-ES";
      recognition.continuous = false;
      recognition.interimResults = false;
      recognition.maxAlternatives = 1;
      this._recognition = recognition;
      this.micState = "recording";
      this.micError = "";

      recognition.onresult = (e) => {
        const transcript = (e.results[0][0].transcript || "").trim();
        this.micState = "idle";
        const titleEl = document.getElementById("new-event-summary");
        if (titleEl) {
          titleEl.value = transcript;
          titleEl.dispatchEvent(new Event("input", { bubbles: true }));
        }
        this.createOpen = true;
      };

      recognition.onerror = (e) => {
        if (e.error !== "aborted") {
          this.micError = "No se pudo capturar audio. Comprueba los permisos del micrófono.";
        }
        this.micState = "idle";
      };

      recognition.onend = () => {
        if (this.micState === "recording") this.micState = "idle";
        this._recognition = null;
      };

      recognition.start();
    },

    destroy() {
      this._recognition?.stop();
    },
  }));
}

/**
 * Grabador de micrófono para creación de eventos por voz (Paso 23).
 * Usa MediaRecorder API; si no está disponible el template muestra un
 * <input type="file"> como fallback (progressive enhancement).
 *
 * Estados: idle | recording | uploading | error
 * Al detener, sube el Blob a POST /calendar/voice/transcribe via fetch
 * y reemplaza el contenido de #voice-container con la respuesta HTML.
 */
function registerVoiceRecorder() {
  Alpine.data("voiceRecorder", (maxSeconds = 60) => ({
    micState: "idle",
    errorMsg: "",
    elapsed: 0,
    hasMediaRecorder: typeof window.MediaRecorder !== "undefined",
    _recorder: null,
    _chunks: [],
    _timer: null,

    startRecording() {
      if (this.micState !== "idle") return;
      this.errorMsg = "";
      navigator.mediaDevices
        .getUserMedia({ audio: true })
        .then((stream) => {
          this._chunks = [];
          this._recorder = new MediaRecorder(stream);
          this._recorder.ondataavailable = (e) => {
            if (e.data && e.data.size > 0) this._chunks.push(e.data);
          };
          this._recorder.onstop = () => {
            stream.getTracks().forEach((t) => t.stop());
            const blob = new Blob(this._chunks, {
              type: this._recorder.mimeType || "audio/webm",
            });
            this._uploadAudio(blob, this._recorder.mimeType || "audio/webm");
          };
          this._recorder.start(250); // collect data every 250ms
          this.micState = "recording";
          this.elapsed = 0;
          this._timer = setInterval(() => {
            this.elapsed += 1;
            if (this.elapsed >= maxSeconds) this.stopRecording();
          }, 1000);
        })
        .catch(() => {
          this.micState = "error";
          this.errorMsg =
            "No se pudo acceder al micrófono. Comprueba los permisos del navegador.";
        });
    },

    stopRecording() {
      if (this._timer) {
        clearInterval(this._timer);
        this._timer = null;
      }
      if (this._recorder && this._recorder.state !== "inactive") {
        this._recorder.stop();
      }
      this.micState = "uploading";
    },

    async _uploadAudio(blob, mimeType) {
      const fd = new FormData();
      const ext = mimeType.includes("ogg") ? "ogg" : mimeType.includes("mp4") ? "mp4" : "webm";
      fd.append("audio", blob, `recording.${ext}`);
      try {
        const resp = await fetch("/calendar/voice/transcribe", {
          method: "POST",
          body: fd,
          headers: { "HX-Request": "true" },
        });
        const html = await resp.text();
        const container = document.getElementById("voice-container");
        if (container) {
          container.innerHTML = html;
          if (window.htmx) window.htmx.process(container);
          if (window.Alpine) window.Alpine.initTree(container);
        }
      } catch {
        this.micState = "error";
        this.errorMsg = "Error al enviar el audio. Inténtalo de nuevo.";
      }
    },

    formatElapsed() {
      const m = String(Math.floor(this.elapsed / 60)).padStart(2, "0");
      const s = String(this.elapsed % 60).padStart(2, "0");
      return `${m}:${s}`;
    },

    destroy() {
      if (this._timer) clearInterval(this._timer);
      if (this._recorder && this._recorder.state !== "inactive") {
        this._recorder.stop();
      }
    },
  }));
}

registerHtmxAlpineBridge();
registerChatComposerClear();

document.addEventListener("alpine:init", () => {
  registerInvoicesTableColumns();
  registerChatMessagesScroll();
  registerCalendarEventsPanel();
  registerVoiceRecorder();
});
