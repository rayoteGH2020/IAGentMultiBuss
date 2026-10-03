/**
 * Opciones comunes de clerk-js para auth embebido.
 *
 * Producto: 1 usuario ≈ 1 org. Sin selector multi-org en UI.
 * Si Clerk deja la sesion sin org activa (task choose-organization o
 * lastActiveOrganizationId vacio) pero el usuario tiene exactamente una
 * membership, se llama setActive sobre esa org. Solo si hay 0 orgs (o >1)
 * se redirige a /onboarding.
 */
(function (global) {
  "use strict";

  var NO_ORG_PATH = "/onboarding";
  var HOME_PATH = "/";

  function loadOptions(extra) {
    var base = {
      signInUrl: "/login",
      signUpUrl: "/signup",
      afterSignInUrl: "/",
      afterSignUpUrl: "/",
      afterSignOutUrl: "/login",
      taskUrls: {
        // Pantalla de aviso si no hay org; si hay exactamente una, onboarding
        // ejecuta setActive y vuelve a /.
        "choose-organization": NO_ORG_PATH,
      },
    };
    if (!extra) return base;
    var merged = Object.assign({}, base, extra);
    if (extra.taskUrls) {
      merged.taskUrls = Object.assign({}, base.taskUrls, extra.taskUrls);
    }
    return merged;
  }

  function taskKey(session) {
    if (!session || !session.currentTask) return null;
    var task = session.currentTask;
    if (typeof task === "string") return task;
    return task.key || null;
  }

  function activeOrganizationId(clerk) {
    if (!clerk || !clerk.session) return null;
    return (
      clerk.session.lastActiveOrganizationId ||
      (clerk.organization && clerk.organization.id) ||
      null
    );
  }

  function membershipOrganizationId(membership) {
    if (!membership || typeof membership !== "object") return null;
    var org = membership.organization || membership;
    var id = org.id || membership.organizationId || null;
    return typeof id === "string" && id ? id : null;
  }

  async function listOrganizationMemberships(clerk) {
    var user = clerk && clerk.user;
    if (!user) return [];

    if (typeof user.getOrganizationMemberships === "function") {
      try {
        var page = await user.getOrganizationMemberships();
        if (Array.isArray(page)) return page;
        if (page && Array.isArray(page.data)) return page.data;
      } catch (_err) {
        /* fall through */
      }
    }

    var resource = user.organizationMemberships;
    if (Array.isArray(resource)) return resource;
    if (resource && Array.isArray(resource.data)) return resource.data;
    return [];
  }

  async function resolveSoleOrganizationId(clerk) {
    var memberships = await listOrganizationMemberships(clerk);
    if (memberships.length !== 1) return null;
    return membershipOrganizationId(memberships[0]);
  }

  /**
   * Asegura org activa en sesion Clerk.
   * @returns {Promise<{status: string, organizationId?: string}>}
   */
  async function ensureActiveOrganization(clerk) {
    if (!clerk || !clerk.session) return { status: "no-session" };

    var current = activeOrganizationId(clerk);
    if (current) return { status: "active", organizationId: current };

    var soleId = await resolveSoleOrganizationId(clerk);
    if (!soleId) return { status: "missing" };

    if (typeof clerk.setActive !== "function") return { status: "missing" };

    await clerk.setActive({ organization: soleId });
    var after = activeOrganizationId(clerk) || soleId;
    return { status: "activated", organizationId: after };
  }

  function needsNoOrgRedirect(clerk) {
    if (!clerk) return false;
    var session = clerk.session;
    if (!session) return false;
    if (taskKey(session) === "choose-organization") return true;
    if (session.status === "active" && !activeOrganizationId(clerk)) return true;
    return false;
  }

  /** Sync helper legacy: solo decide redirect si ya no hay org activa. */
  function redirectIfNoOrg(clerk) {
    if (!needsNoOrgRedirect(clerk)) return false;
    if (global.location.pathname === NO_ORG_PATH) return false;
    global.location.replace(NO_ORG_PATH);
    return true;
  }

  /**
   * Intenta setActive(unica org); solo entonces manda a /onboarding.
   * Si estamos en /onboarding y se activa, vuelve a /.
   * @returns {Promise<boolean>} true si navego a otra ruta
   */
  async function ensureOrgOrRedirect(clerk) {
    var result = await ensureActiveOrganization(clerk);

    if (result.status === "active" || result.status === "activated") {
      if (global.location.pathname === NO_ORG_PATH) {
        global.location.replace(HOME_PATH);
        return true;
      }
      return false;
    }

    if (result.status === "no-session") return false;

    if (global.location.pathname === NO_ORG_PATH) return false;
    global.location.replace(NO_ORG_PATH);
    return true;
  }

  function watchNoOrgRedirect(clerk) {
    if (!clerk || typeof clerk.addListener !== "function") return;
    var busy = false;
    clerk.addListener(function () {
      if (busy) return;
      busy = true;
      Promise.resolve(ensureOrgOrRedirect(clerk))
        .catch(function () {
          return false;
        })
        .finally(function () {
          busy = false;
        });
    });
  }

  global.MiSaaSClerkAuth = {
    NO_ORG_PATH: NO_ORG_PATH,
    loadOptions: loadOptions,
    needsNoOrgRedirect: needsNoOrgRedirect,
    redirectIfNoOrg: redirectIfNoOrg,
    ensureActiveOrganization: ensureActiveOrganization,
    ensureOrgOrRedirect: ensureOrgOrRedirect,
    watchNoOrgRedirect: watchNoOrgRedirect,
  };
})(window);
