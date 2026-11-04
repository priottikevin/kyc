(() => {
  const API = "/api/admin";
  const $ = (id) => document.getElementById(id);
  let selected = null;

  const date = (v) => (v ? new Date(v).toLocaleDateString("fr-FR") : "—");
  const dateTime = (v) => (v ? new Date(v).toLocaleString("fr-FR", { dateStyle: "short", timeStyle: "short" }) : "Jamais");
  const usd = (v) => `${Number(v || 0).toFixed(4)} $`;

  function showError(message) {
    $("error").textContent = message || "";
    $("error").hidden = !message;
  }

  async function call(path, method = "GET", body) {
    const r = await fetch(API + path, {
      method,
      credentials: "same-origin",
      headers: body ? { "Content-Type": "application/json" } : {},
      body: body ? JSON.stringify(body) : undefined,
    });
    const data = await r.json().catch(() => ({}));
    if (r.status === 401 && path !== "/login") {
      showLogin();
      throw new Error("Session expirée, reconnectez-vous");
    }
    if (!r.ok) throw new Error(typeof data.detail === "string" ? data.detail : `Erreur ${r.status}`);
    return data;
  }

  // Every handler goes through here so that a failed call shows up on the page
  const guarded = (fn) => async (event) => {
    event?.preventDefault();
    showError("");
    try {
      await fn(event);
    } catch (err) {
      showError(err.message);
    }
  };

  function cell(row, text, className) {
    const td = document.createElement("td");
    td.textContent = text;
    if (className) td.className = className;
    row.append(td);
    return td;
  }

  function badge(row, active, on, off) {
    const span = document.createElement("span");
    span.className = active ? "admin-badge ok" : "admin-badge off";
    span.textContent = active ? on : off;
    cell(row, "").append(span);
  }

  function fill(tbodyId, emptyId, items, draw) {
    const tbody = $(tbodyId);
    tbody.replaceChildren(...items.map(draw));
    $(emptyId).hidden = items.length > 0;
    tbody.closest("table").hidden = items.length === 0;
  }

  function clientRow(c) {
    const tr = document.createElement("tr");
    const nameCell = cell(tr, "");
    const open = document.createElement("button");
    open.type = "button";
    open.className = "admin-link";
    open.textContent = c.name;
    open.addEventListener("click", guarded(() => select(c)));
    nameCell.append(open);
    cell(tr, c.contact_email || "—");
    badge(tr, c.active, "Actif", "Désactivé");
    cell(tr, c.active_keys, "num");
    cell(tr, c.calls, "num");
    cell(tr, c.successes, "num");
    cell(tr, usd(c.llm_cost_usd), "num");
    if (selected?.id === c.id) tr.className = "selected";
    return tr;
  }

  function keyRow(k) {
    const tr = document.createElement("tr");
    cell(tr, k.name);
    cell(tr, `${k.prefix}…`, "admin-mono");
    cell(tr, date(k.created_at));
    cell(tr, dateTime(k.last_used_at));
    badge(tr, !k.revoked_at, "Active", "Révoquée");
    const actions = cell(tr, "");
    if (!k.revoked_at) {
      const revoke = document.createElement("button");
      revoke.type = "button";
      revoke.className = "ghost admin-danger";
      revoke.textContent = "Révoquer";
      revoke.addEventListener("click", guarded(async () => {
        if (!confirm(`Révoquer la clé « ${k.name} » ? Les appels qui l'utilisent échoueront immédiatement.`)) return;
        await call(`/keys/${k.id}`, "DELETE");
        await refresh();
      }));
      actions.append(revoke);
    }
    return tr;
  }

  function usageRow(u) {
    const tr = document.createElement("tr");
    cell(tr, u.month);
    cell(tr, u.calls, "num");
    cell(tr, u.successes, "num");
    cell(tr, u.llm_calls, "num");
    cell(tr, usd(u.llm_cost_usd), "num");
    return tr;
  }

  async function refresh() {
    const clients = await call("/clients");
    if (selected) selected = clients.find((c) => c.id === selected.id) || null;
    fill("clients", "noClients", clients, clientRow);
    $("detail").hidden = !selected;
    if (!selected) return;
    $("detailName").textContent = selected.name;
    $("toggleActive").textContent = selected.active ? "Désactiver le client" : "Réactiver le client";
    const [keys, usage] = await Promise.all([call(`/clients/${selected.id}/keys`), call(`/clients/${selected.id}/usage`)]);
    fill("keys", "noKeys", keys, keyRow);
    fill("usage", "noUsage", usage, usageRow);
  }

  async function select(client) {
    selected = client;
    $("newKey").hidden = true;
    await refresh();
    $("detail").scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function showLogin() {
    selected = null;
    $("app").hidden = true;
    $("logout").hidden = true;
    $("loginForm").hidden = false;
    $("password").focus();
  }

  async function showApp() {
    $("loginForm").hidden = true;
    $("app").hidden = false;
    $("logout").hidden = false;
    await refresh();
  }

  $("loginForm").addEventListener("submit", guarded(async () => {
    await call("/login", "POST", { password: $("password").value });
    $("password").value = "";
    await showApp();
  }));

  $("logout").addEventListener("click", guarded(async () => {
    await call("/logout", "POST");
    showLogin();
  }));

  $("clientForm").addEventListener("submit", guarded(async () => {
    await call("/clients", "POST", { name: $("clientName").value, contact_email: $("clientEmail").value || null });
    $("clientForm").reset();
    await refresh();
  }));

  $("keyForm").addEventListener("submit", guarded(async () => {
    const created = await call(`/clients/${selected.id}/keys`, "POST", { name: $("keyName").value });
    $("keyForm").reset();
    $("newKeyValue").textContent = created.key;
    $("newKey").hidden = false;
    $("copyKey").textContent = "Copier";
    await refresh();
  }));

  $("copyKey").addEventListener("click", guarded(async () => {
    await navigator.clipboard.writeText($("newKeyValue").textContent);
    $("copyKey").textContent = "Copié";
  }));

  $("toggleActive").addEventListener("click", guarded(async () => {
    const active = !selected.active;
    if (!active && !confirm(`Désactiver ${selected.name} ? Toutes ses clés cesseront de fonctionner immédiatement.`)) return;
    await call(`/clients/${selected.id}`, "PATCH", { active });
    await refresh();
  }));

  guarded(async () => {
    try {
      await call("/session");
    } catch {
      showLogin();
      showError("");
      return;
    }
    await showApp();
  })();
})();
