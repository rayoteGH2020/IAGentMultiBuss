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

/** HTMX inserta HTML sin procesar; Alpine debe reinicializar el subárbol del swap.
 *  Solo afterSettle (una vez) y destroyTree antes de initTree evita listeners duplicados
 *  que rompían el primer click de la navegación tras un boost. */
function registerHtmxAlpineBridge() {
  const initAlpineOnSwap = (event) => {
    const target = event.detail?.target;
    if (!(target instanceof Element)) return;
    if (!window.Alpine) return;
    window.Alpine.destroyTree(target);
    window.Alpine.initTree(target);
  };
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
          headers: {
            "HX-Request": "true",
            "X-CSRF-Token": window.getCsrfToken?.() || "",
          },
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

/** Horario semanal del centro: minutos según granularidad del tenant y copiar fila al día siguiente. */
function registerBusinessHoursTable() {
  const SLOT_SUFFIXES = ["sort_0_opens", "sort_0_closes", "sort_1_opens", "sort_1_closes"];

  function parseMinuteOptions(root) {
    const raw = root.dataset.minuteOptions;
    if (!raw) return ["00", "15", "30", "45"];
    try {
      const parsed = JSON.parse(raw);
      return Array.isArray(parsed) && parsed.length ? parsed.map(String) : ["00"];
    } catch {
      return ["00", "15", "30", "45"];
    }
  }

  Alpine.data("quarterHourTime", () => ({
    weekday: 0,
    slot: "",
    hour: "",
    minute: "00",
    minuteOptions: ["00"],

    init() {
      const root = this.$el;
      this.weekday = Number(root.dataset.weekday ?? 0);
      this.slot = root.dataset.slot ?? "";
      this.minuteOptions = parseMinuteOptions(root);
      this.minute = this.minuteOptions[0] ?? "00";
      this.setValue(root.dataset.value ?? "");
    },

    get fieldName() {
      return `weekday_${this.weekday}_${this.slot}`;
    },

    get combined() {
      if (!this.hour) return "";
      return `${this.hour}:${this.minute}`;
    },

    setValue(value) {
      if (!value) {
        this.hour = "";
        this.minute = "00";
        return;
      }
      const [h, m] = String(value).split(":");
      this.hour = h || "";
      this.minute = this.minuteOptions.includes(m) ? m : (this.minuteOptions[0] ?? "00");
    },
  }));

  Alpine.data("businessHoursTable", () => ({
    formError: "",
    weekdayLabels: ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"],
    periodPairs: [
      ["sort_0_opens", "sort_0_closes", "mañana"],
      ["sort_1_opens", "sort_1_closes", "tarde"],
    ],

    init() {
      const root = this.$el;
      if (root.dataset.weekdayLabels) {
        try {
          const parsed = JSON.parse(root.dataset.weekdayLabels);
          if (Array.isArray(parsed) && parsed.length === 7) {
            this.weekdayLabels = parsed.map(String);
          }
        } catch {
          /* keep defaults */
        }
      }
    },

    slotValue(weekday, slotSuffix) {
      const hidden = document.querySelector(
        `input[type="hidden"][name="weekday_${weekday}_${slotSuffix}"]`,
      );
      return hidden instanceof HTMLInputElement ? hidden.value.trim() : "";
    },

    validateBeforeSave() {
      this.formError = "";
      for (let weekday = 0; weekday < 7; weekday += 1) {
        for (const [opensKey, closesKey, periodLabel] of this.periodPairs) {
          const opens = this.slotValue(weekday, opensKey);
          const closes = this.slotValue(weekday, closesKey);
          if (Boolean(opens) !== Boolean(closes)) {
            const dayLabel = this.weekdayLabels[weekday] ?? String(weekday);
            this.formError = `${dayLabel}: indica inicio y fin del horario de ${periodLabel}, o déjalo vacío`;
            return false;
          }
        }
      }
      return true;
    },

    copyRowToNext(fromWeekday) {
      const toWeekday = Number(fromWeekday) + 1;
      SLOT_SUFFIXES.forEach((slot) => {
        const fromHidden = document.querySelector(
          `input[type="hidden"][name="weekday_${fromWeekday}_${slot}"]`,
        );
        const value = fromHidden instanceof HTMLInputElement ? fromHidden.value : "";
        const toHidden = document.querySelector(
          `input[type="hidden"][name="weekday_${toWeekday}_${slot}"]`,
        );
        const targetRoot = toHidden?.closest("[x-data]");
        if (targetRoot && window.Alpine) {
          const data = window.Alpine.$data(targetRoot);
          if (data && typeof data.setValue === "function") {
            data.setValue(value);
          }
        }
      });
    },

    clearRow(weekday) {
      SLOT_SUFFIXES.forEach((slot) => {
        const hidden = document.querySelector(
          `input[type="hidden"][name="weekday_${weekday}_${slot}"]`,
        );
        const targetRoot = hidden?.closest("[x-data]");
        if (targetRoot && window.Alpine) {
          const data = window.Alpine.$data(targetRoot);
          if (data && typeof data.setValue === "function") {
            data.setValue("");
          }
        }
      });
      this.formError = "";
    },
  }));
}

/** Inicio de cita: fecha + hora/minuto solo desde franjas del grid del centro. */
function registerAppointmentStartPicker() {
  Alpine.data("appointmentStartPicker", () => ({
    date: "",
    hour: "",
    minute: "",
    gridTimesByWeekday: {},
    closedDates: [],

    init() {
      const root = this.$el;
      this.gridTimesByWeekday = JSON.parse(root.dataset.gridTimes || "{}");
      this.closedDates = JSON.parse(root.dataset.closedDates || "[]");
      this.date = root.dataset.defaultDate || "";
      this.hour = root.dataset.defaultHour || "";
      this.minute = root.dataset.defaultMinute || "";
      this.$watch("date", () => this.syncSelection());
      this.$watch("hour", () => this.syncMinute());
      this.syncSelection();
      this.$watch("hasSlots", () => this.notifyValidity());
      this.$watch("closedDateSelected", () => this.notifyValidity());
      this.notifyValidity();
    },

    notifyValidity() {
      this.$dispatch("appointment-grid-slots", {
        valid: this.hasSlots && !this.closedDateSelected,
      });
    },

    pythonWeekday() {
      if (!this.date) return null;
      const parts = this.date.split("-").map(Number);
      if (parts.length !== 3) return null;
      const jsDay = new Date(parts[0], parts[1] - 1, parts[2]).getDay();
      return (jsDay + 6) % 7;
    },

    get availableSlots() {
      const weekday = this.pythonWeekday();
      if (weekday === null) return [];
      return (
        this.gridTimesByWeekday[String(weekday)] ||
        this.gridTimesByWeekday[weekday] ||
        []
      );
    },

    get hourOptions() {
      return [...new Set(this.availableSlots.map((slot) => slot.split(":")[0]))].sort();
    },

    get minuteOptions() {
      if (!this.hour) return [];
      return this.availableSlots
        .filter((slot) => slot.startsWith(`${this.hour}:`))
        .map((slot) => slot.split(":")[1])
        .sort();
    },

    get startTime() {
      if (!this.hour || !this.minute) return "";
      return `${this.hour}:${this.minute}`;
    },

    get hasSlots() {
      return this.availableSlots.length > 0;
    },

    get closedDateSelected() {
      return Boolean(this.date && this.closedDates.includes(this.date));
    },

    syncSelection() {
      if (!this.hourOptions.length) {
        this.hour = "";
        this.minute = "";
        this.notifyValidity();
        return;
      }
      if (!this.hourOptions.includes(this.hour)) {
        this.hour = this.hourOptions[0];
      }
      this.syncMinute();
    },

    syncMinute() {
      if (!this.minuteOptions.length) {
        this.minute = "";
        this.notifyValidity();
        return;
      }
      if (!this.minuteOptions.includes(this.minute)) {
        this.minute = this.minuteOptions[0];
      }
      this.notifyValidity();
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
  registerBusinessHoursTable();
  registerAppointmentStartPicker();
  registerProfessionalHoursGrid();
  registerKnowledgeUploadForm();
  registerTenantSearchSelect();
});

/**
 * Combobox SADM: elegir tenant con filtro por texto (nombre o id).
 * Al seleccionar navega a basePath/{id}.
 */
function registerTenantSearchSelect() {
  Alpine.data("tenantSearchSelect", (config = {}) => ({
    tenants: Array.isArray(config.tenants) ? config.tenants : [],
    selectedId: config.selectedId || null,
    basePath: config.basePath || "/sadm/chat-usage",
    open: false,
    query: "",
    highlight: 0,

    get selectedLabel() {
      if (!this.selectedId) return "";
      const hit = this.tenants.find((t) => String(t.id) === String(this.selectedId));
      return hit ? hit.name : "";
    },

    get filtered() {
      const q = this.query.trim().toLowerCase();
      if (!q) return this.tenants;
      return this.tenants.filter((t) => {
        const name = String(t.name || "").toLowerCase();
        const id = String(t.id || "").toLowerCase();
        const plan = String(t.plan || "").toLowerCase();
        return name.includes(q) || id.includes(q) || plan.includes(q);
      });
    },

    toggle() {
      this.open = !this.open;
      if (this.open) {
        this.query = "";
        this.highlight = 0;
        this.$nextTick(() => this.$refs.query?.focus());
      }
    },

    close() {
      this.open = false;
      this.query = "";
      this.highlight = 0;
    },

    onQueryInput() {
      this.highlight = 0;
      if (!this.open) this.open = true;
    },

    move(delta) {
      const n = this.filtered.length;
      if (n === 0) return;
      this.highlight = (this.highlight + delta + n) % n;
    },

    selectHighlighted() {
      const item = this.filtered[this.highlight];
      if (item) this.select(item);
    },

    select(tenant) {
      if (!tenant?.id) return;
      this.selectedId = tenant.id;
      this.close();
      window.location.assign(`${this.basePath}/${tenant.id}`);
    },
  }));
}

/** Modal de subida knowledge: adjunto de ficheros + cierre solo si el servidor crea docs. */
function registerKnowledgeUploadForm() {
  Alpine.data("knowledgeUploadForm", () => ({
    open: false,
    files: [],
    dragging: false,
    kind: "",
    uploadInputKey: 0,
    formError: "",
    isMobile: window.matchMedia("(hover: none) and (pointer: coarse)").matches,

    openModal() {
      this.open = true;
      this.files = [];
      this.kind = "";
      this.formError = "";
      this.uploadInputKey += 1;
    },

    closeModal() {
      this.open = false;
    },

    onUploadResult(detail) {
      if (detail && detail.ok) {
        this.open = false;
        this.files = [];
        this.kind = "";
        this.formError = "";
        this.uploadInputKey += 1;
        return;
      }
      this.formError =
        (detail && detail.message) || "No se pudo subir el documento. Revisa los datos.";
    },

    onFilePick(event) {
      this.files = Array.from(event.target.files || []);
      this.formError = "";
    },

    onDrop(event) {
      this.dragging = false;
      this.files = Array.from(event.dataTransfer.files || []);
      this.formError = "";
      this.syncInputFiles();
    },

    onCameraCapture(event) {
      const captured = Array.from(event.target.files || []);
      if (captured.length > 0) {
        this.files = captured;
        this.kind = "";
        this.formError = "";
        this.uploadInputKey += 1;
        this.open = true;
      }
      event.target.value = "";
    },

    syncInputFiles() {
      const dt = new DataTransfer();
      this.files.forEach((f) => dt.items.add(f));
      if (this.$refs.kinput) {
        this.$refs.kinput.files = dt.files;
      }
    },

    prepareRequest(event) {
      if (this.files.length === 0 || !this.kind) {
        event.preventDefault();
        this.formError =
          this.files.length === 0
            ? "Selecciona al menos un fichero."
            : "Selecciona una categoría para el documento.";
        return;
      }
      this.formError = "";
      const fd = event.detail.formData;
      if (!fd) return;
      fd.delete("files");
      this.files.forEach((f) => fd.append("files", f, f.name));
    },
  }));
}

/** Grid horario profesional: marcar/desmarcar tramos del centro. */
function registerProfessionalHoursGrid() {
  Alpine.data("professionalHoursGrid", () => ({
    slotCheckboxes() {
      return Array.from(document.querySelectorAll(".professional-slot-checkbox"));
    },

    selectAll() {
      this.slotCheckboxes().forEach((el) => {
        el.checked = true;
      });
    },

    clearAll() {
      this.slotCheckboxes().forEach((el) => {
        el.checked = false;
      });
    },

    selectPeriod(weekday, sortOrder) {
      this.slotCheckboxes()
        .filter(
          (el) =>
            Number(el.dataset.weekday) === Number(weekday) &&
            Number(el.dataset.sortOrder) === Number(sortOrder),
        )
        .forEach((el) => {
          el.checked = true;
        });
    },

    clearPeriod(weekday, sortOrder) {
      this.slotCheckboxes()
        .filter(
          (el) =>
            Number(el.dataset.weekday) === Number(weekday) &&
            Number(el.dataset.sortOrder) === Number(sortOrder),
        )
        .forEach((el) => {
          el.checked = false;
        });
    },
  }));
}
