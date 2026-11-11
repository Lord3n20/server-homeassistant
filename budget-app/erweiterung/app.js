// Seite der Erweiterung: meldet sich in einem eigenen Tab bei der Bank an, liest die Umsätze
// und schickt sie nach Bestätigung an den Webhook der Budget-App.
const $ = (s) => document.querySelector(s);
const pause = (ms) => new Promise((r) => setTimeout(r, ms));
const eur = (b) => Number(b).toLocaleString("de-DE", { style: "currency", currency: "EUR" });
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

let bank = "tr", bankTab = null, gefunden = [], codeWartet = null;
const BANKEN = {
  tr: { name: "Trade Republic", start: "https://app.traderepublic.com/login", kennung: "Telefonnummer" },
  gls: { name: "GLS Bank", start: "https://www.gls-online-filiale.de/services_cloud/portal", kennung: "VR-NetKey oder Alias" },
};

function status(text, fehler = false) {
  $("#status").textContent = text;
  $("#status").className = fehler ? "fehler" : "";
}

// ---------- Hilfen für den Bank-Tab ----------
async function inSeite(func, ...args) {
  try {
    const [r] = await chrome.scripting.executeScript({ target: { tabId: bankTab }, world: "MAIN", func, args });
    return r?.result;
  } catch (e) {
    if (/No tab|closed/i.test(e.message)) throw new Error("Der Bank-Tab wurde geschlossen");
    return undefined; // Seite lädt gerade neu
  }
}

async function warteAuf(func, args = [], sekunden = 60, schritt = 700) {
  const ende = Date.now() + sekunden * 1000;
  while (Date.now() < ende) {
    const r = await inSeite(func, ...args);
    if (r) return r;
    await pause(schritt);
  }
  return null;
}

// ---------- Funktionen, die in der Bankseite laufen (müssen in sich geschlossen sein) ----------
function seiteFeldSetzen(selektoren, werte, absenden) {
  const finde = (sel, root = document) => {
    const e = root.querySelector(sel);
    if (e) return e;
    for (const x of root.querySelectorAll("*")) if (x.shadowRoot) { const t = finde(sel, x.shadowRoot); if (t) return t; }
    return null;
  };
  const setze = (el, wert) => {
    el.focus();
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
    setter.call(el, wert);
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
    el.dispatchEvent(new Event("blur", { bubbles: true }));
  };
  const felder = selektoren.map((s) => finde(s));
  if (felder.some((f) => !f)) return false;
  felder.forEach((f, i) => setze(f, werte[i]));
  if (absenden) {
    const knopf = [...document.querySelectorAll("button[type=submit], button")].find((b) => b.offsetParent && new RegExp(absenden, "i").test((b.innerText || "") + " " + (b.getAttribute("aria-label") || "")));
    if (knopf) setTimeout(() => knopf.click(), 150);
    else felder[felder.length - 1].form?.requestSubmit?.();
  }
  return true;
}

function seiteZiffernFelder() {
  // Sichtbare Eingabefelder für PIN oder Code (ohne das Telefonfeld).
  return [...document.querySelectorAll("input")]
    .filter((i) => i.offsetParent && i.name !== "username" && (i.type === "password" || i.type === "tel" || i.inputMode === "numeric" || i.maxLength === 1))
    .length;
}

function seiteZiffernEingeben(ziffern) {
  const felder = [...document.querySelectorAll("input")]
    .filter((i) => i.offsetParent && i.name !== "username" && (i.type === "password" || i.type === "tel" || i.inputMode === "numeric" || i.maxLength === 1));
  if (!felder.length) return false;
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
  const eins = (el, wert) => {
    el.focus();
    setter.call(el, wert);
    el.dispatchEvent(new InputEvent("input", { bubbles: true, data: wert, inputType: "insertText" }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
  };
  if (felder.length >= ziffern.length && felder.every((f) => f.maxLength === 1)) ziffern.split("").forEach((z, i) => eins(felder[i], z));
  else eins(felder[0], ziffern);
  return true;
}

function seiteTrZustand() {
  if (!location.pathname.startsWith("/login")) return { drin: true };
  const telefon = document.querySelector('input[name="username"]');
  const fehler = [...document.querySelectorAll('[role="alert"], .errorMessage, [class*="error"]')].map((e) => e.innerText.trim()).filter(Boolean).join(" ").slice(0, 200);
  return { telefon: !!(telefon && telefon.offsetParent), text: document.body.innerText.slice(0, 600), fehler };
}

async function seiteTrUmsaetze(tage) {
  const chrome = (navigator.userAgent.match(/Chrome\/([\d.]+)/) || [])[1] || "";
  const verbinden = window.__budgetTrConnect || `connect 31 ${JSON.stringify({ locale: "de", platformId: "webtrading", platformVersion: `chrome - ${chrome}`, clientId: "app.traderepublic.com", clientVersion: "5582" })}`;
  const ws = new WebSocket("wss://api.traderepublic.com");
  const eingang = [], wartende = [];
  ws.onmessage = (e) => {
    const i = wartende.findIndex((w) => w.passt(e.data));
    if (i >= 0) wartende.splice(i, 1)[0].fertig(e.data); else eingang.push(e.data);
  };
  const naechste = (passt, ms = 20000) => new Promise((fertig, fehlt) => {
    const i = eingang.findIndex(passt);
    if (i >= 0) return fertig(eingang.splice(i, 1)[0]);
    const w = { passt, fertig };
    wartende.push(w);
    setTimeout(() => { const j = wartende.indexOf(w); if (j >= 0) { wartende.splice(j, 1); fehlt(new Error("Trade Republic antwortet nicht")); } }, ms);
  });
  await new Promise((ok, fehlt) => { ws.onopen = ok; ws.onerror = () => fehlt(new Error("Keine Verbindung zu Trade Republic")); });
  ws.send(verbinden);
  const hallo = await naechste(() => true);
  if (hallo !== "connected") throw new Error("Trade Republic: " + String(hallo).slice(0, 120));
  const grenze = Date.now() - tage * 86400000;
  const zeit = (t) => Date.parse(String(t).replace(/([+-]\d\d)(\d\d)$/, "$1:$2"));
  const posten = [];
  let after = null, nr = 9000;
  for (let seite = 0; seite < 60; seite++) {
    nr++;
    ws.send(`sub ${nr} ${JSON.stringify(after ? { type: "timelineTransactions", after } : { type: "timelineTransactions" })}`);
    const kopf = `${nr} `;
    const roh = await naechste((m) => m.startsWith(kopf));
    ws.send(`unsub ${nr}`);
    const art = roh[kopf.length], inhalt = roh.slice(kopf.length + 1).trim();
    if (art === "E") throw new Error("Trade Republic: " + inhalt.slice(0, 160));
    const daten = JSON.parse(inhalt || "{}");
    let zuAlt = false;
    for (const p of daten.items || []) {
      if (zeit(p.timestamp) < grenze) { zuAlt = true; break; }
      posten.push(p);
    }
    after = daten.cursors?.after;
    if (zuAlt || !after) break;
  }
  ws.close();
  const tag = (t) => { const d = new Date(zeit(t)); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`; };
  return posten
    .filter((p) => p.amount && typeof p.amount.value === "number" && p.amount.value !== 0 && String(p.status || "").toUpperCase() !== "CANCELED")
    .map((p) => ({ id: p.id, datum: tag(p.timestamp), betrag: p.amount.value.toFixed(2), name: p.title || "", zweck: p.subtitle || "" }));
}

function seiteGlsZustand() {
  const login = document.querySelector("#vrNetKey");
  const fehler = [...document.querySelectorAll('[role="alert"], app-alert-receiver, mat-error')].map((e) => e.innerText.trim()).filter(Boolean).join(" ").slice(0, 200);
  return { login: !!login, portal: location.pathname.startsWith("/services_cloud/portal") && !login, fehler, text: document.body.innerText.slice(0, 400) };
}

function seiteGlsUmsaetzeOeffnen() {
  const ziel = [...document.querySelectorAll("a, button")].find((e) => e.offsetParent && /^\s*Umsätze\s*$/i.test(e.innerText || ""))
    || [...document.querySelectorAll("a, button")].find((e) => e.offsetParent && /Umsätze/i.test(e.innerText || ""));
  if (ziel) ziel.click();
  return !!ziel;
}

function seiteGlsUmsaetze() {
  // Sucht in den mitgeschnittenen JSON-Antworten die Liste, die am meisten nach Umsätzen aussieht.
  const DATUM = /^(\d{4}-\d{2}-\d{2})|^(\d{2})\.(\d{2})\.(\d{4})$/;
  const flach = (o, vor = "", tiefe = 0, aus = {}) => {
    for (const [k, v] of Object.entries(o || {})) {
      const name = vor ? `${vor}.${k}` : k;
      if (v && typeof v === "object" && !Array.isArray(v) && tiefe < 2) flach(v, name, tiefe + 1, aus);
      else aus[name] = v;
    }
    return aus;
  };
  const zahl = (v) => {
    if (typeof v === "number") return v;
    if (typeof v !== "string") return NaN;
    const t = v.replace(/[€\sA-Z]/g, "");
    return Number(t.includes(",") ? t.replace(/\./g, "").replace(",", ".") : t);
  };
  const isoDatum = (v) => {
    const m = String(v).match(DATUM);
    if (!m) return "";
    return m[1] || `${m[4]}-${m[3]}-${m[2]}`;
  };
  const schluessel = (f, muster, pruef) => Object.keys(f).find((k) => muster.test(k) && pruef(f[k]));
  const listen = [];
  const sammeln = (x, tiefe = 0) => {
    if (tiefe > 8 || !x || typeof x !== "object") return;
    if (Array.isArray(x)) {
      if (x.length && x.every((e) => e && typeof e === "object" && !Array.isArray(e))) listen.push(x);
      x.forEach((e) => sammeln(e, tiefe + 1));
    } else Object.values(x).forEach((v) => sammeln(v, tiefe + 1));
  };
  for (const a of window.__budgetGlsAntworten || []) sammeln(a.daten);
  let beste = null;
  for (const liste of listen) {
    const f = flach(liste[0]);
    const k = {
      datum: schluessel(f, /buchung|booking|valuta|value.?date|datum|date/i, (v) => !!isoDatum(v)),
      betrag: schluessel(f, /betrag|amount|umsatz|value/i, (v) => !Number.isNaN(zahl(v))),
      name: schluessel(f, /name|empf|auftrag|partner|gegen|counterpart|creditor|debtor|zahlungsbeteiligt/i, (v) => typeof v === "string"),
      zweck: schluessel(f, /zweck|purpose|verwendung|remittance|buchungstext|beschreibung|description|text/i, (v) => typeof v === "string"),
      soll: schluessel(f, /soll.?haben|credit.?debit|indicator|richtung|direction/i, (v) => typeof v === "string"),
    };
    if (!k.datum || !k.betrag) continue;
    if (!beste || liste.length > beste.liste.length) beste = { liste, k };
  }
  if (!beste) return { posten: [], felder: listen.slice(0, 5).map((l) => Object.keys(flach(l[0]))) };
  const { liste, k } = beste;
  const posten = liste.map((e) => {
    const f = flach(e);
    let betrag = zahl(f[k.betrag]);
    if (k.soll && /^(s|d|dbit|debit|soll)/i.test(String(f[k.soll])) && betrag > 0) betrag = -betrag;
    return { datum: isoDatum(f[k.datum]), betrag: betrag.toFixed(2), name: k.name ? f[k.name] : "", zweck: k.zweck ? f[k.zweck] : "" };
  }).filter((p) => p.datum && Number(p.betrag) !== 0);
  return { posten, felder: [Object.keys(flach(liste[0]))], zuordnung: k };
}

// ---------- Abläufe ----------
async function tabOeffnen() {
  const tab = await chrome.tabs.create({ url: BANKEN[bank].start, active: false });
  bankTab = tab.id;
  await pause(1500);
}

async function trAblauf(kennung, pin, tage) {
  let nummer = kennung.replace(/[\s/-]/g, "");
  nummer = nummer.replace(/^(\+49|0049)/, "").replace(/^0/, "");
  status("Öffne Trade Republic …");
  await tabOeffnen();
  const z = await warteAuf(seiteTrZustand, [], 40);
  if (!z) throw new Error("Trade Republic lädt nicht");
  if (!z.drin) {
    if (!(await warteAuf(seiteFeldSetzen, [['input[name="username"]'], [nummer], "weiter"], 20))) throw new Error("Telefonfeld nicht gefunden");
    status("Telefonnummer eingegeben, warte auf PIN-Feld …");
    if (!(await warteAuf(seiteZiffernFelder, [], 30))) throw new Error("PIN-Feld nicht gefunden: " + ((await inSeite(seiteTrZustand))?.fehler || ""));
    await pause(400);
    await inSeite(seiteZiffernEingeben, pin);
    status("Bitte in der Trade-Republic-App bestätigen (oder unten den Code eingeben, falls einer verlangt wird).");
    $("#codeZeile").hidden = false;
    const ende = Date.now() + 180000;
    while (Date.now() < ende) {
      const s = await inSeite(seiteTrZustand);
      if (s?.drin) break;
      if (codeWartet) { await inSeite(seiteZiffernEingeben, codeWartet); codeWartet = null; }
      await pause(1000);
    }
    $("#codeZeile").hidden = true;
    if (!(await inSeite(seiteTrZustand))?.drin) throw new Error("Anmeldung nicht bestätigt");
  }
  status("Angemeldet, lese Umsätze …");
  await pause(1500);
  const r = await chrome.scripting.executeScript({ target: { tabId: bankTab }, world: "MAIN", func: seiteTrUmsaetze, args: [tage] });
  if (r[0]?.error) throw new Error(r[0].error.message || String(r[0].error));
  return r[0]?.result || [];
}

async function glsAblauf(kennung, pin, tage) {
  status("Öffne das GLS-Onlinebanking …");
  await tabOeffnen();
  const z = await warteAuf(seiteGlsZustand, [], 40);
  if (!z) throw new Error("GLS-Seite lädt nicht");
  if (!z.portal) {
    if (!(await warteAuf(() => !!document.querySelector("#vrNetKey"), [], 30))) throw new Error("Anmeldefeld nicht gefunden");
    await inSeite(seiteFeldSetzen, ["#vrNetKey", "#pin"], [kennung, pin], "^\\s*Anmelden\\s*$");
    status("Bitte in SecureGo plus freigeben …");
    const ende = Date.now() + 300000;
    let s;
    while (Date.now() < ende) {
      s = await inSeite(seiteGlsZustand);
      if (s?.portal) break;
      if (s?.login && s.fehler) throw new Error("GLS: " + s.fehler);
      await pause(1500);
    }
    if (!s?.portal) throw new Error("Anmeldung nicht freigegeben");
  }
  status("Angemeldet, öffne die Umsätze …");
  await pause(3000);
  await warteAuf(seiteGlsUmsaetzeOeffnen, [], 20, 1500);
  return glsLesen(tage);
}

async function glsLesen(tage) {
  let r = null;
  const ende = Date.now() + 20000;
  while (Date.now() < ende) {
    r = await inSeite(seiteGlsUmsaetze);
    if (r?.posten?.length) break;
    await pause(1500);
  }
  $("#felder").textContent = r?.felder ? `Gefundene Felder (für die Fehlersuche): ${r.felder.map((f) => f.join(", ")).join(" | ")}` : "";
  if (!r?.posten?.length) {
    $("#hilfe").hidden = false;
    throw new Error("Keine Umsätze erkannt. Bitte im Bank-Tab die Umsätze des Girokontos öffnen und dann „Nochmal lesen“.");
  }
  const grenze = new Date(Date.now() - tage * 86400000).toISOString().slice(0, 10);
  return r.posten.filter((p) => p.datum >= grenze);
}

function zeigeErgebnis(liste) {
  gefunden = liste;
  $("#ergebnis").hidden = false;
  $("#ergebnisTitel").textContent = `${liste.length} Umsätze von ${BANKEN[bank].name}`;
  $("#liste").innerHTML = liste.map((p) => `<tr><td class="gedaempft">${esc(p.datum.slice(8))}.${esc(p.datum.slice(5, 7))}.</td><td>${esc(p.name)}<div class="gedaempft">${esc(String(p.zweck).slice(0, 80))}</div></td><td class="zahl">${eur(p.betrag)}</td></tr>`).join("");
  status("Bitte prüfen und senden. In der Budget-App ordnest du sie danach ein.");
}

async function tabZu() {
  if (bankTab && $("#schliessen").checked) { try { await chrome.tabs.remove(bankTab); } catch (e) {} }
  bankTab = null;
}

$("#anmelden").onsubmit = async (e) => {
  e.preventDefault();
  const kennung = $("#kennung").value.trim(), pin = $("#pin").value, tage = Number($("#tage").value);
  $("#pin").value = "";
  $("#los").disabled = true; $("#hilfe").hidden = true; $("#ergebnis").hidden = true;
  try {
    const liste = bank === "tr" ? await trAblauf(kennung, pin, tage) : await glsAblauf(kennung, pin, tage);
    zeigeErgebnis(liste);
    await tabZu();
  } catch (ex) {
    status(ex.message, true);
    if (bank === "tr") await tabZu();
  } finally {
    $("#los").disabled = false; $("#codeZeile").hidden = true;
  }
};

$("#codeSenden").onclick = () => { codeWartet = $("#code").value.trim(); $("#code").value = ""; };
$("#nochmal").onclick = async () => {
  try { zeigeErgebnis(await glsLesen(Number($("#tage").value))); $("#hilfe").hidden = true; await tabZu(); }
  catch (ex) { status(ex.message, true); }
};
$("#zeigen").onclick = () => bankTab && chrome.tabs.update(bankTab, { active: true });
$("#verwerfen").onclick = () => { gefunden = []; $("#ergebnis").hidden = true; status(""); };

$("#senden").onclick = async () => {
  const url = $("#webhook").value.trim();
  $("#senden").disabled = true;
  try {
    const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ bank, umsaetze: gefunden }) });
    if (!r.ok) throw new Error(`Budget-App antwortet mit ${r.status}`);
    status(`${gefunden.length} Umsätze gesendet. Neue erscheinen in der Budget-App im Reiter „Bank“.`);
    gefunden = []; $("#ergebnis").hidden = true;
  } catch (ex) {
    status("Senden ging nicht: " + ex.message, true);
  } finally {
    $("#senden").disabled = false;
  }
};

for (const b of document.querySelectorAll(".reiter button")) {
  b.onclick = () => {
    bank = b.dataset.bank;
    document.querySelectorAll(".reiter button").forEach((x) => x.setAttribute("aria-pressed", x === b));
    $("#kennungText").textContent = BANKEN[bank].kennung;
    $("#kennung").value = ""; $("#pin").value = "";
    $("#kennung").type = bank === "tr" ? "tel" : "text";
  };
}

chrome.storage.local.get("webhook").then(({ webhook }) => { $("#webhook").value = webhook || (typeof WEBHOOK_STANDARD !== "undefined" ? WEBHOOK_STANDARD : ""); });
$("#webhook").onchange = () => chrome.storage.local.set({ webhook: $("#webhook").value.trim() });
$("#kennung").type = "tel";
