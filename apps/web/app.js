(() => {
  const API = (window.KYC_API_BASE || "") + "/api";
  const $ = (id) => document.getElementById(id);
  const drop = $("drop"), fileInput = $("file");
  const KEY = "kyc_api_key";

  const LABELS = {
    surname: "Nom", usage_name: "Nom d'usage", given_names: "Prénoms", full_name: "Nom complet", sex: "Sexe",
    nationality: "Nationalité", birth_date: "Date de naissance", birth_place: "Lieu de naissance",
    document_number: "N° de document", document_code: "Code document", issuing_country: "Pays émetteur",
    issue_date: "Date de délivrance", issue_month: "Mois de délivrance", expiry_date: "Date d'expiration",
    issuing_authority: "Autorité", address: "Adresse", optional_data: "Données optionnelles",
    license_number: "N° de permis", categories: "Catégories",
    plate: "Immatriculation", vin: "VIN", make: "Marque", commercial_name: "Modèle", type_variant: "Type",
    holder_name: "Titulaire", holder_address: "Adresse titulaire", first_registration_date: "1ère immatriculation",
    certificate_date: "Date du certificat", fuel: "Énergie", fuel_label: "Énergie (libellé)",
    fiscal_power_cv: "Puissance fiscale (CV)", engine_capacity_cm3: "Cylindrée (cm³)", seats: "Places assises",
    national_category: "Genre national", max_mass_kg: "PTAC (kg)", mass_in_service_kg: "Masse en service (kg)",
    insurer: "Assureur", policy_number: "N° de contrat", valid_from: "Valable du", valid_to: "Valable au",
    vehicle_make: "Marque véhicule", vehicle_model: "Modèle véhicule", job_title: "Fonction", company: "Société",
    emails: "E-mails", phones: "Téléphones", websites: "Sites web", siren_siret: "SIREN / SIRET",
    height: "Taille", holder_status: "Qualité du titulaire", tax_regional: "Taxe régionale (€)", tax_total: "Taxe totale (€)",
    // Kbis
    company_name: "Dénomination", siren: "SIREN", rcs_city: "RCS", registration_date: "Date d'immatriculation",
    legal_form: "Forme juridique", share_capital: "Capital social", euid: "EUID", head_office_address: "Siège",
    activity: "Activité", activity_start_date: "Début d'activité", management_number: "N° de gestion",
    extract_date: "Extrait à jour au", legal_representatives: "Dirigeants",
    // invoice / bank
    invoice_number: "N° de facture", invoice_date: "Date de facture", due_date: "Échéance", supplier_name: "Fournisseur",
    supplier_siret: "SIRET fournisseur", vat_number: "N° TVA", total_excl_tax: "Total HT", total_vat: "TVA",
    total_incl_tax: "Total TTC", iban: "IBAN", bic: "BIC", bank_name: "Banque", statement_period: "Période",
    statement_date: "Date du relevé", account_holder: "Titulaire du compte", closing_balance: "Nouveau solde",
    // claims
    report_number: "N° de rapport", report_date: "Date du rapport", claim_number: "N° de sinistre", claim_date: "Date du sinistre",
    expert_name: "Expert", conclusion: "Conclusion", repairer_invoice_number: "N° facture réparateur",
    order_number: "N° d'OR", opened_date: "Ouvert le", work_type: "Type de travaux", customer_name: "Client",
    work_description: "Travaux", contract_number: "N° de contrat", subject: "Objet", insurer_reference: "Référence assureur",
    liability: "Responsabilité",
  };
  const AMOUNTS = /^(total_|closing_balance)/;

  const getKey = () => { try { return localStorage.getItem(KEY) || ""; } catch { return ""; } };
  const setKey = (v) => { try { localStorage.setItem(KEY, v); } catch {} };

  // ---- health & types
  fetch(API + "/health").then(r => r.json()).then(h => {
    const el = $("health");
    el.className = "health ok";
    el.textContent = h.llm === "off" ? "API prête · IA désactivée" : `API prête · IA ${h.llm}`;
    if (h.auth && !getKey()) $("keyForm").hidden = false;
  }).catch(() => { $("health").className = "health down"; $("health").textContent = "API injoignable"; });

  fetch(API + "/document-types").then(r => r.json()).then(types => {
    for (const [k, v] of Object.entries(types)) {
      if (k === "unknown") continue;
      const o = document.createElement("option");
      o.value = k; o.textContent = v; $("docType").append(o);
    }
  }).catch(() => {});

  // ---- API key
  $("keyBtn").onclick = () => { $("keyForm").hidden = !$("keyForm").hidden; $("keyInput").value = getKey(); };
  $("keyForm").onsubmit = (e) => { e.preventDefault(); setKey($("keyInput").value.trim()); $("keyForm").hidden = true; };

  // ---- file input
  fileInput.onchange = () => fileInput.files[0] && send(fileInput.files[0]);
  drop.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fileInput.click(); } });
  ["dragenter", "dragover"].forEach(ev => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach(ev => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
  drop.addEventListener("drop", (e) => { const f = e.dataTransfer.files[0]; if (f) send(f); });
  window.addEventListener("paste", (e) => {
    const f = [...(e.clipboardData?.files || [])][0];
    if (f) send(f);
  });

  let lastFile = null;

  async function send(file) {
    lastFile = file;
    $("error").hidden = true;
    drop.classList.add("busy");
    const fd = new FormData();
    fd.append("file", file);
    if ($("docType").value) fd.append("document_type", $("docType").value);
    if ($("llm").value) fd.append("llm", $("llm").value);
    const headers = {};
    if (getKey()) headers["X-API-Key"] = getKey();
    const t0 = performance.now();
    try {
      const r = await fetch(API + "/extract", { method: "POST", body: fd, headers });
      const body = await r.json().catch(() => ({}));
      if (r.status === 401) { $("keyForm").hidden = false; $("keyInput").focus(); throw new Error(body.detail || "Clé API requise"); }
      if (!r.ok) throw new Error(body.detail || `Erreur ${r.status}`);
      render(body, file, Math.round(performance.now() - t0));
    } catch (err) {
      $("error").textContent = err.message;
      $("error").hidden = false;
    } finally {
      drop.classList.remove("busy");
      fileInput.value = "";
    }
  }

  // re-run with new options on the same file
  ["docType", "llm"].forEach(id => $(id).addEventListener("change", () => lastFile && send(lastFile)));

  function render(r, file, roundtrip) {
    $("result").hidden = false;
    $("docLabel").textContent = r.document_label;
    const st = $("status");
    st.className = "badge " + r.status;
    st.textContent = { verified: "Vérifié (checksums)", complete: "Complet", partial: "Partiel" }[r.status] || r.status;

    const e = r.engine;
    const meta = [file.name, e.input.toUpperCase(), `${e.pages} page${e.pages > 1 ? "s" : ""}`, "OCR " + e.ocr_passes.join(" → ")];
    if (e.llm_used) meta.push(`IA ${e.llm_model} (${e.llm_kind === "lines" ? "relecture des lignes douteuses" : "document"})`);
    if (r.mrz) meta.push(`MRZ ${r.mrz.format} ${r.mrz.valid ? "✓" : "✗"}`);
    $("meta").textContent = meta.join(" · ");

    const tm = $("timings");
    tm.innerHTML = "";
    const add = (label, ms, cls) => { const s = document.createElement("span"); s.textContent = `${label} ${ms} ms`; if (cls) s.className = cls; tm.append(s); };
    add("serveur", r.timings_ms.total, "total");
    for (const [k, v] of Object.entries(r.timings_ms)) if (k !== "total") add(k, v);
    add("aller-retour", roundtrip);

    const w = $("warnings");
    w.innerHTML = "";
    for (const msg of [...(r.missing_fields.length ? [`Champs manquants : ${r.missing_fields.join(", ")}`] : []), ...r.warnings]) {
      const li = document.createElement("li"); li.textContent = msg; w.append(li);
    }

    const tb = $("fields");
    tb.innerHTML = "";
    for (const [k, v] of Object.entries(r.fields)) {
      const tr = document.createElement("tr");
      const td1 = document.createElement("td"); td1.textContent = LABELS[k] || k;
      const td2 = document.createElement("td"); td2.className = "val copyable"; td2.title = "Cliquer pour copier";
      const fmt = (x) => (x && typeof x === "object") ? Object.values(x).join(" : ") : String(x);
      const text = Array.isArray(v) ? v.map(fmt).join(", ")
        : (typeof v === "number" && AMOUNTS.test(k)) ? v.toLocaleString("fr-FR", { minimumFractionDigits: 2 }) + " €"
        : fmt(v);
      td2.textContent = text;
      td2.onclick = () => navigator.clipboard?.writeText(text);
      const c = (r.field_confidence || {})[k];
      if (c) {
        const b = document.createElement("span");
        b.className = "conf " + c.level;
        const pct = c.score == null ? "" : ` ${Math.floor(c.score * 100)} %`;
        b.textContent = {
          verified: "vérifié ✓", high: "fiable" + pct, confirmed: "confirmé IA" + pct,
          llm: "lu par l'IA", check: "à vérifier" + pct, unknown: "à vérifier",
        }[c.level] || c.level;
        b.title = {
          verified: "Prouvé par une somme de contrôle (MRZ, SIREN, IBAN, totaux)",
          high: "Tous les caractères lus avec une confiance ≥ 97 %",
          confirmed: "Un caractère était douteux ; l'IA a relu la ligne et lit la même chose",
          llm: "Valeur corrigée ou ajoutée par l'IA",
          check: "Au moins un caractère lu avec une confiance < 97 % (score = caractère le plus faible)",
          unknown: "Valeur reformatée ou déduite : pas de score de lecture direct",
        }[c.level] || "";
        td2.append(b);
        if (c.level === "check" || c.level === "unknown") tr.classList.add("doubt");
      }
      const src = r.field_sources[k];
      if (src) {
        const s = document.createElement("span");
        s.className = "src " + (src.startsWith("mrz") ? "mrz" : src === "llm" ? "llm" : "");
        s.textContent = src;
        td2.append(s);
      }
      tr.append(td1, td2); tb.append(tr);
    }

    $("json").textContent = JSON.stringify(r, null, 2);
    $("copy").onclick = (ev) => { ev.preventDefault(); navigator.clipboard?.writeText($("json").textContent); ev.target.textContent = "Copié"; setTimeout(() => ev.target.textContent = "Copier", 1200); };

    const pv = $("preview");
    pv.innerHTML = "";
    if (file.type.startsWith("image/")) {
      const img = document.createElement("img");
      img.src = URL.createObjectURL(file);
      img.onload = () => URL.revokeObjectURL(img.src);
      pv.append(img);
    } else {
      pv.textContent = "PDF";
    }
  }
})();
