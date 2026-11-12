// Läuft in der Seite der GLS und der TF Bank: hebt JSON-Antworten des Online-Bankings kurz auf,
// damit die Erweiterung die Umsätze daraus lesen kann. Nichts davon verlässt die Seite,
// bis du in der Erweiterung auf Senden drückst.
(() => {
  const ablage = (window.__budgetAntworten = []);
  const merken = (url, text) => {
    try {
      const daten = JSON.parse(text);
      ablage.push({ pfad: new URL(url, location.href).pathname, daten });
      if (ablage.length > 80) ablage.shift();
    } catch (e) {}
  };
  const holen = window.fetch;
  window.fetch = async function (...a) {
    const antwort = await holen.apply(this, a);
    try {
      if ((antwort.headers.get("content-type") || "").includes("json")) antwort.clone().text().then((t) => merken(antwort.url, t));
    } catch (e) {}
    return antwort;
  };
  const oeffnen = XMLHttpRequest.prototype.open;
  XMLHttpRequest.prototype.open = function (m, url, ...rest) {
    this.addEventListener("load", () => {
      try {
        if ((this.getResponseHeader("content-type") || "").includes("json") && (this.responseType === "" || this.responseType === "text")) merken(url, this.responseText);
        else if (this.responseType === "json" && this.response) { ablage.push({ pfad: new URL(url, location.href).pathname, daten: this.response }); }
      } catch (e) {}
    });
    return oeffnen.call(this, m, url, ...rest);
  };
})();
