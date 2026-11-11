// Klick aufs Symbol öffnet die Seite der Erweiterung (oder holt sie nach vorn).
chrome.action.onClicked.addListener(async () => {
  const url = chrome.runtime.getURL("app.html");
  const [offen] = await chrome.tabs.query({ url });
  if (offen) {
    await chrome.tabs.update(offen.id, { active: true });
    await chrome.windows.update(offen.windowId, { focused: true });
  } else {
    await chrome.tabs.create({ url });
  }
});
