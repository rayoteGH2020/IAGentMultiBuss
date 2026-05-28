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

registerHtmxAlpineBridge();
registerChatComposerClear();

document.addEventListener("alpine:init", () => {
  registerInvoicesTableColumns();
  registerChatMessagesScroll();
});
