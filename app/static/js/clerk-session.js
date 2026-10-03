/**
 * Mantiene vigente la cookie `__session` (JWT de Clerk, ~60 s) en las páginas
 * de la app, que no montan componentes de Clerk.
 *
 * Sin esto, tras un rato en segundo plano (el navegador frena los timers de
 * refresco de clerk-js) una petición HTMX llega con el JWT caducado, el
 * middleware manda a /login y el usuario pierde lo que estaba haciendo
 * (p. ej. la subida de documentos).
 *
 * - Antes de cada petición HTMX pide un token vigente (`session.getToken()`
 *   devuelve el cacheado si sigue válido y renueva la cookie si no).
 * - Al volver a la pestaña, lo renueva de inmediato.
 * Si Clerk no carga, las peticiones siguen sin esperar más de TOKEN_WAIT_MS.
 */
(function (global) {
  "use strict";

  var CLERK_WAIT_MS = 8000;
  var TOKEN_WAIT_MS = 3000;
  var ready = null;

  function sleep(ms) {
    return new Promise(function (resolve) {
      setTimeout(resolve, ms);
    });
  }

  function clerkReady() {
    if (ready) return ready;
    ready = (async function () {
      var started = Date.now();
      while (!global.Clerk) {
        if (Date.now() - started > CLERK_WAIT_MS) return null;
        await sleep(50);
      }
      var clerk = global.Clerk;
      if (!clerk.loaded) await clerk.load();
      return clerk;
    })().catch(function () {
      ready = null; // reintenta en la siguiente petición
      return null;
    });
    return ready;
  }

  async function refreshSessionToken() {
    var clerk = await clerkReady();
    if (!clerk || !clerk.session) return;
    try {
      await clerk.session.getToken();
    } catch (_err) {
      /* sin token: el servidor decidirá (redirect a /login con retorno) */
    }
  }

  function withTimeout(promise, ms) {
    return Promise.race([promise, sleep(ms)]);
  }

  document.addEventListener("htmx:confirm", function (event) {
    var detail = event.detail;
    if (!detail || typeof detail.issueRequest !== "function") return;
    event.preventDefault();
    withTimeout(refreshSessionToken(), TOKEN_WAIT_MS).finally(function () {
      // issueRequest(true) omite el hx-confirm nativo de htmx: se replica aquí.
      if (detail.question && !global.confirm(detail.question)) return;
      detail.issueRequest(true);
    });
  });

  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "visible") refreshSessionToken();
  });

  clerkReady();
})(window);
