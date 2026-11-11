// Läuft in der Seite von Trade Republic: merkt sich die Verbindungsnachricht der Web-App,
// damit die Erweiterung ihre eigene Verbindung genauso aufbauen kann.
(() => {
  const senden = WebSocket.prototype.send;
  WebSocket.prototype.send = function (daten) {
    if (typeof daten === "string" && daten.startsWith("connect ") && !window.__budgetTrConnect) window.__budgetTrConnect = daten;
    return senden.call(this, daten);
  };
})();
