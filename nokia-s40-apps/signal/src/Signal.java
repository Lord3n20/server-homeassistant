import com.nokia.mid.ui.DeviceControl;

import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.util.Calendar;
import java.util.Enumeration;
import java.util.Hashtable;
import java.util.Vector;

import javax.microedition.io.Connector;
import javax.microedition.io.HttpConnection;
import javax.microedition.io.PushRegistry;
import javax.microedition.io.file.FileConnection;
import javax.microedition.io.file.FileSystemRegistry;
import javax.microedition.lcdui.Alert;
import javax.microedition.lcdui.AlertType;
import javax.microedition.lcdui.Canvas;
import javax.microedition.lcdui.Choice;
import javax.microedition.lcdui.ChoiceGroup;
import javax.microedition.lcdui.Command;
import javax.microedition.lcdui.CommandListener;
import javax.microedition.lcdui.Display;
import javax.microedition.lcdui.Displayable;
import javax.microedition.lcdui.Font;
import javax.microedition.lcdui.Form;
import javax.microedition.lcdui.Graphics;
import javax.microedition.lcdui.Image;
import javax.microedition.lcdui.ImageItem;
import javax.microedition.lcdui.Item;
import javax.microedition.lcdui.ItemCommandListener;
import javax.microedition.lcdui.List;
import javax.microedition.lcdui.StringItem;
import javax.microedition.lcdui.TextBox;
import javax.microedition.lcdui.TextField;
import javax.microedition.media.Manager;
import javax.microedition.media.MediaException;
import javax.microedition.media.Player;
import javax.microedition.media.control.VideoControl;
import javax.microedition.midlet.MIDlet;
import javax.microedition.rms.RecordStore;

/**
 * Signal on the Nokia through the nokia-signal bridge app in Home Assistant.
 * Every request is encrypted (see Crypto); one worker thread does the
 * network work for the screens, a second one long-polls the bridge and
 * triggers a refresh as soon as something arrives. The 6303i has no
 * background MIDlets, so while the app is closed a PushRegistry alarm
 * starts it every few minutes: it checks the chat list without showing
 * anything and only comes to the front if there is something new.
 * Written for CLDC 1.1 / MIDP 2.0 with JSR-75 and JSR-135, so Java 1.3
 * syntax only: no generics, no foreach, no String.split.
 */
public class Signal extends MIDlet implements CommandListener, ItemCommandListener, Runnable {
    private static final String STORE = "signal";
    private static final String TITLE = "Signal";
    private static final int MAX_UPLOAD = 3 * 1024 * 1024;
    /** Messages per answer of the bridge. */
    private static final int PAGE = 30;
    /** Messages kept per chat in the phone memory. */
    private static final int CACHE = 40;

    private final Command cmdRefresh = new Command("Aktualisieren", Command.SCREEN, 3);
    private final Command cmdSettings = new Command("Einstellungen", Command.SCREEN, 4);
    private final Command cmdExit = new Command("Beenden", Command.EXIT, 5);
    private final Command cmdSave = new Command("Speichern", Command.OK, 1);
    private final Command cmdBack = new Command("Zurück", Command.BACK, 2);
    private final Command cmdWrite = new Command("Schreiben", Command.SCREEN, 1);
    private final Command cmdPhoto = new Command("Foto aufnehmen", Command.SCREEN, 2);
    private final Command cmdFile = new Command("Bild senden", Command.SCREEN, 3);
    private final Command cmdOlder = new Command("Ältere laden", Command.SCREEN, 4);
    private final Command cmdSend = new Command("Senden", Command.OK, 1);
    private final Command cmdCancel = new Command("Abbrechen", Command.BACK, 2);
    private final Command cmdView = new Command("Ansehen", Command.ITEM, 1);
    private final Command cmdZoom = new Command("Zoom", Command.SCREEN, 1);
    private final Command cmdShoot = new Command("Auslösen", Command.OK, 1);

    private Display display;
    private List chats;
    private Form settings;
    private TextField fieldUrl;
    private TextField fieldKey;

    private String baseUrl = "";
    private String key = "";
    private Crypto crypto;
    /** Server clock minus phone clock, learned from the bridge. */
    private long offset;

    /** One String[] {conv, name, unread, when, preview} per chat. */
    private Vector chatRows = new Vector();
    private int unreadTotal = -1;

    private Form chat;
    private String chatId;
    private String chatName;
    private int firstId;
    private int lastId;
    private Hashtable imageLinks = new Hashtable();
    /** Raw lines of the open chat, oldest first, for the cache. */
    private Vector chatLines = new Vector();
    private TextBox compose;

    private List files;
    private String filePath;
    private Form confirm;
    private String sendFile;
    private byte[] sendPhoto;

    private ImageView viewer;
    private CameraView camera;

    private final Vector jobs = new Vector();
    private boolean quiet;
    /** Set by the watcher when the bridge reports news. */
    private boolean changed;
    /** News came in while the open chat was in the background (guarded by jobs). */
    private boolean missed;
    /** Bridge change counter seen last; -1 = not asked yet. */
    private int seenVersion = -1;
    /** The worker is running a job (guarded by jobs). */
    private boolean busy;

    /** Wake-up choices in the settings, in minutes; 0 = off. */
    private static final int[] WAKE_MINUTES = {0, 5, 10, 15, 30};
    private ChoiceGroup fieldWake;

    /** How often the open app asks for news, in seconds; 0 = at once (long poll), -1 = by hand. */
    private static final int[] POLL_SECONDS = {0, 60, 300, -1};
    private static final String[] POLL_NAMES = {"sofort", "jede Minute", "alle 5 min", "nur von Hand"};
    private ChoiceGroup fieldPoll;
    private int pollSeconds = 0;

    /** Sound on news: 0 = vibration only, 1 = soft tone without vibration, 2 = alarm tone. */
    private static final String[] SOUND_NAMES = {"nur Vibration", "leiser Ton", "lauter Ton"};
    private ChoiceGroup fieldSound;
    private int sound = 0;
    /** Minutes between background checks while the app is closed. */
    private int wakeMinutes = 0;
    /** When the phone starts the app next; 0 = no alarm. */
    private long wakeAt;
    /** When the alarm was last pushed ahead. */
    private long wakeArmed;
    /** Result of the last background check, shown in the settings. */
    private String wakeLog = "";
    /** When this run of the app started; stays old if the red key keeps it alive. */
    private long startedAt;
    /** Last answer of the long poll while the app was in the background; 0 = never. */
    private long lastBackground;

    public void startApp() {
        if (display != null) {
            return;
        }
        display = Display.getDisplay(this);
        startedAt = System.currentTimeMillis();
        chats = new List(TITLE, List.IMPLICIT);
        chats.setFitPolicy(Choice.TEXT_WRAP_ON);
        chats.addCommand(cmdRefresh);
        chats.addCommand(cmdSettings);
        chats.addCommand(cmdExit);
        chats.setCommandListener(this);

        loadSettings();
        loadWake();
        String[] cached = cacheRead("chats", "chats");
        if (cached != null) {
            StringBuffer sb = new StringBuffer();
            for (int i = 0; i < cached.length; i++) {
                sb.append(cached[i]).append('\n');
            }
            showChats(sb.toString(), false);
        }
        long now = System.currentTimeMillis();
        if (key.length() > 0 && wakeAt > 0 && now >= wakeAt - 5000 && now < wakeAt + 120000) {
            // Started by the alarm, not by hand: nothing on screen yet.
            new Thread(new Runnable() {
                public void run() {
                    check();
                }
            }).start();
            return;
        }
        display.setCurrent(chats);
        open();
        if (key.length() == 0) {
            showSettings();
        } else {
            start();
        }
    }

    /** Starts the worker and the long poll, for an app that is on screen. */
    private void open() {
        armWake();
        new Thread(this).start();
        new Thread(new Runnable() {
            public void run() {
                watch();
            }
        }).start();
    }

    /**
     * Background check after the alarm: one request for the chat list. More
     * unread than at the last look brings the app to the front with the
     * usual note and buzz; otherwise it quits again without ever showing.
     */
    private void check() {
        String result;
        try {
            crypto = new Crypto(key);
            String answer = text(call("chats", null, null));
            cacheWrite("chats", "chats", split(answer, '\n'), 0);
            int total = 0;
            String[] lines = split(answer, '\n');
            for (int i = 0; i < lines.length; i++) {
                String[] f = split(lines[i], '\t');
                if (f.length >= 5) {
                    total += Integer.parseInt(f[2]);
                }
            }
            if (total > Math.max(unreadTotal, 0)) {
                wakeLog = clock(System.currentTimeMillis()) + " Neues gemeldet";
                unreadTotal = Math.max(unreadTotal, 0);
                showChats(answer, true); // notifyNew brings the list to the front
                open();
                return;
            }
            result = "nichts Neues";
        } catch (Throwable t) {
            result = "Fehler: " + (t.getMessage() == null ? t.toString() : t.getMessage());
        }
        wakeLog = clock(System.currentTimeMillis()) + " " + result;
        armWake();
        notifyDestroyed();
    }

    public void pauseApp() {
    }

    public void destroyApp(boolean unconditional) {
        // Red key: keep checking in the background.
        armWake();
    }

    private void start() {
        crypto = new Crypto(key);
        enqueue(new Runnable() {
            public void run() {
                String[] f = split(text(call("ping", null, null)), '\t');
                if ("koppeln".equals(f[0])) {
                    error("Die Brücke ist noch nicht mit Signal gekoppelt (QR-Code in Home Assistant).");
                }
                loadChats();
            }
        });
    }

    // --- commands --------------------------------------------------------

    public void commandAction(Command c, Displayable d) {
        if (c == cmdExit) {
            armWake();
            notifyDestroyed();
        } else if (c == cmdRefresh) {
            final boolean inChat = d == chat;
            enqueue(new Runnable() {
                public void run() {
                    if (inChat) {
                        loadMessages(true);
                    }
                    loadChats();
                }
            });
        } else if (c == cmdSettings) {
            showSettings();
        } else if (c == cmdSave) {
            baseUrl = trimSlash(fieldUrl.getString().trim());
            key = fieldKey.getString().trim();
            pollSeconds = POLL_SECONDS[Math.max(0, fieldPoll.getSelectedIndex())];
            sound = Math.max(0, fieldSound.getSelectedIndex());
            saveSettings();
            wakeMinutes = WAKE_MINUTES[Math.max(0, fieldWake.getSelectedIndex())];
            armWake();
            display.setCurrent(chats);
            start();
        } else if (c == List.SELECT_COMMAND && d == chats) {
            int i = chats.getSelectedIndex();
            if (i >= 0 && i < chatRows.size()) {
                String[] row = (String[]) chatRows.elementAt(i);
                openChat(row[0], row[1]);
            }
        } else if (c == cmdWrite) {
            display.setCurrent(compose);
        } else if (c == cmdSend && d == compose) {
            sendText(compose.getString());
        } else if (c == cmdOlder) {
            enqueue(new Runnable() {
                public void run() {
                    loadMessages(false);
                }
            });
        } else if (c == cmdFile) {
            browse(null);
        } else if (c == List.SELECT_COMMAND && d == files) {
            if (files.getSelectedIndex() >= 0) {
                fileChosen(files.getString(files.getSelectedIndex()));
            }
        } else if (c == cmdPhoto) {
            final CameraView cam = new CameraView();
            camera = cam;
            display.setCurrent(cam);
            enqueue(new Runnable() {
                public void run() {
                    cam.open();
                }
            });
        } else if (c == cmdShoot) {
            enqueue(new Runnable() {
                public void run() {
                    shoot();
                }
            });
        } else if (c == cmdSend && d == confirm) {
            sendImage();
        } else if (c == cmdZoom) {
            viewer.zoom();
        } else if (c == cmdCancel || c == cmdBack) {
            back(d);
        }
    }

    public void commandAction(Command c, Item item) {
        String att = (String) imageLinks.get(item);
        if (c == cmdView && att != null) {
            viewer = new ImageView(att);
            display.setCurrent(viewer);
            viewer.load();
        }
    }

    private void back(Displayable d) {
        if (d == chat || d == settings) {
            chat = d == chat ? null : chat;
            display.setCurrent(chats);
        } else if (d == camera) {
            camera.close();
            camera = null;
            display.setCurrent(chat);
        } else if (d == files && filePath != null) {
            browse(parent(filePath));
        } else if (chat != null) {
            display.setCurrent(chat);
        } else {
            display.setCurrent(chats);
        }
    }

    private void showSettings() {
        settings = new Form("Einstellungen");
        fieldUrl = new TextField("Adresse", baseUrl, 100, TextField.URL);
        fieldKey = new TextField("Schlüssel", key, 64, TextField.NON_PREDICTIVE);
        fieldWake = new ChoiceGroup("Prüfen, wenn geschlossen", Choice.POPUP);
        for (int i = 0; i < WAKE_MINUTES.length; i++) {
            fieldWake.append(WAKE_MINUTES[i] == 0 ? "aus" : "alle " + WAKE_MINUTES[i] + " min", null);
            if (WAKE_MINUTES[i] == wakeMinutes) {
                fieldWake.setSelectedIndex(i, true);
            }
        }
        settings.append(fieldUrl);
        settings.append(fieldKey);
        fieldPoll = new ChoiceGroup("Neues holen, wenn offen", Choice.POPUP);
        for (int i = 0; i < POLL_SECONDS.length; i++) {
            fieldPoll.append(POLL_NAMES[i], null);
            if (POLL_SECONDS[i] == pollSeconds) {
                fieldPoll.setSelectedIndex(i, true);
            }
        }
        settings.append(fieldPoll);
        fieldSound = new ChoiceGroup("Bei neuer Nachricht", Choice.POPUP);
        for (int i = 0; i < SOUND_NAMES.length; i++) {
            fieldSound.append(SOUND_NAMES[i], null);
        }
        fieldSound.setSelectedIndex(sound, true);
        settings.append(fieldSound);
        settings.append(fieldWake);
        settings.append(new StringItem("Wecker", (wakeAt > 0 ? "nächster " + clock(wakeAt) : "aus")
                + (wakeLog.length() > 0 ? "\nzuletzt " + wakeLog : "")));
        settings.append(new StringItem("Hintergrund", "App läuft seit " + clock(startedAt)
                + (lastBackground > 0 ? "\nim Hintergrund verbunden " + clock(lastBackground)
                        : "\nim Hintergrund noch nie verbunden")));
        settings.addCommand(cmdSave);
        settings.addCommand(cmdBack);
        settings.setCommandListener(this);
        display.setCurrent(settings);
    }

    // --- chat list -------------------------------------------------------

    private void loadChats() {
        String answer = text(call("chats", null, null));
        showChats(answer, true);
        cacheWrite("chats", "chats", split(answer, '\n'), 0);
    }

    /** Fills the chat list from an answer of the bridge (or the cache). */
    private void showChats(String answer, boolean live) {
        String[] lines = split(answer, '\n');
        Vector rows = new Vector();
        int total = 0;
        for (int i = 0; i < lines.length; i++) {
            String[] f = split(lines[i], '\t');
            if (f.length >= 5) {
                rows.addElement(f);
                total += Integer.parseInt(f[2]);
            }
        }
        String selected = null;
        int sel = chats.getSelectedIndex();
        if (sel >= 0 && sel < chatRows.size()) {
            selected = ((String[]) chatRows.elementAt(sel))[0];
        }
        chatRows = rows;
        chats.deleteAll();
        for (int i = 0; i < rows.size(); i++) {
            String[] f = (String[]) rows.elementAt(i);
            String label = f[1];
            if (!"0".equals(f[2])) {
                label += " (" + f[2] + ")";
            }
            chats.append(label + " \u2013 " + oneLine(f[4]), null);
            if (f[0].equals(selected)) {
                chats.setSelectedIndex(i, true);
            }
        }
        if (!live) {
            unreadTotal = total; // news since the last run count as new
            return;
        }
        if (unreadTotal >= 0 && total > unreadTotal) {
            notifyNew();
        }
        unreadTotal = total;
    }

    // --- chat ------------------------------------------------------------

    private void openChat(String conv, String name) {
        chatId = conv;
        chatName = name;
        firstId = 0;
        lastId = 0;
        imageLinks.clear();
        chatLines = new Vector();
        // Messages from the last visit appear at once, without the network.
        Form form = newChatForm(name);
        Item last = null;
        String[] cached = cacheRead(cacheName(conv), conv);
        for (int i = 0; cached != null && i < cached.length; i++) {
            String[] f = split(cached[i], '\t');
            if (f.length < 6) {
                continue;
            }
            Item[] items = messageItems(f);
            for (int j = 0; j < items.length; j++) {
                form.append(items[j]);
                last = items[j];
            }
            int id = Integer.parseInt(f[0]);
            if (firstId == 0 || id < firstId) {
                firstId = id;
            }
            lastId = Math.max(lastId, id);
            chatLines.addElement(cached[i]);
        }
        chat = form;
        compose = new TextBox("An " + name, "", 2000, TextField.ANY);
        compose.addCommand(cmdSend);
        compose.addCommand(cmdCancel);
        compose.setCommandListener(this);
        display.setCurrent(form);
        if (last != null) {
            display.setCurrentItem(last);
        }
        enqueue(new Runnable() {
            public void run() {
                loadMessages(true);
            }
        });
    }

    private Form newChatForm(String name) {
        Form form = new Form(name);
        form.addCommand(cmdWrite);
        form.addCommand(cmdPhoto);
        form.addCommand(cmdFile);
        form.addCommand(cmdOlder);
        form.addCommand(cmdRefresh);
        form.addCommand(cmdBack);
        form.setCommandListener(this);
        return form;
    }

    /**
     * newer: what came after the last shown message, otherwise the page
     * before the first one. An empty chat (or one that missed more than a
     * page) gets the newest page, built off screen because the phone redraws
     * a visible form after every single item.
     */
    private void loadMessages(boolean newer) {
        Form form = chat;
        String conv = chatId;
        if (form == null) {
            return;
        }
        boolean notify = newer && quiet;
        if (lastId == 0) {
            newer = false;
        }
        boolean append = newer || firstId == 0;
        Form target = form.size() == 0 ? newChatForm(chatName) : form;
        boolean incoming = false;
        boolean added = false;
        Item last = null;
        int at = 0;
        int lineAt = 0;
        while (true) {
            String req = "msgs\t" + conv + "\t" + (newer ? lastId : 0) + "\t" + (newer ? 0 : firstId);
            String[] lines = split(text(call(req, null, null)), '\n');
            if (form != chat) {
                return; // the chat was closed meanwhile
            }
            if (newer && lines.length >= PAGE) {
                // Too much missed: start over with the newest page.
                firstId = 0;
                lastId = 0;
                chatLines.removeAllElements();
                imageLinks.clear();
                target = newChatForm(chatName);
                newer = false;
                append = true;
                continue;
            }
            for (int i = 0; i < lines.length; i++) {
                String[] f = split(lines[i], '\t');
                if (f.length < 6) {
                    continue;
                }
                int id = Integer.parseInt(f[0]);
                if (append ? id <= lastId : id >= firstId) {
                    continue;
                }
                Item[] items = messageItems(f);
                for (int j = 0; j < items.length; j++) {
                    if (append) {
                        target.append(items[j]);
                    } else {
                        target.insert(at++, items[j]);
                    }
                    last = items[j];
                }
                if (append) {
                    chatLines.addElement(lines[i]);
                } else {
                    chatLines.insertElementAt(lines[i], lineAt++);
                }
                if (firstId == 0 || id < firstId) {
                    firstId = id;
                }
                lastId = Math.max(lastId, id);
                incoming |= "0".equals(f[1]);
                added = true;
            }
            break;
        }
        boolean shown = display.getCurrent() == form;
        if (target != form) {
            chat = target;
            if (shown && last == null) {
                display.setCurrent(target);
            }
        }
        if (notify && incoming) {
            notifyNew();
        }
        if (last != null && append && shown) {
            display.setCurrentItem(last); // also brings a new form on screen
        }
        if (added) {
            cacheWrite(cacheName(conv), conv, chatLines, CACHE);
        }
    }

    /** f = {id, out, sender, when, body, image ids} */
    private Item[] messageItems(String[] f) {
        boolean out = "1".equals(f[1]);
        String who = out ? "Du" : f[2].length() > 0 ? f[2] : chatName;
        String body = f[4].replace('\u001e', '\n');
        String[] imgs = f[5].length() > 0 ? split(f[5], ',') : new String[0];
        Item[] items = new Item[1 + imgs.length];
        StringItem msg = new StringItem(who + ", " + f[3], body);
        msg.setLayout(Item.LAYOUT_NEWLINE_BEFORE | Item.LAYOUT_NEWLINE_AFTER
                | (out ? Item.LAYOUT_RIGHT : Item.LAYOUT_LEFT));
        items[0] = msg;
        for (int i = 0; i < imgs.length; i++) {
            StringItem link = new StringItem(null, "[Bild ansehen]", Item.HYPERLINK);
            link.setLayout(Item.LAYOUT_NEWLINE_BEFORE | Item.LAYOUT_NEWLINE_AFTER
                    | (out ? Item.LAYOUT_RIGHT : Item.LAYOUT_LEFT));
            link.setDefaultCommand(cmdView);
            link.setItemCommandListener(this);
            imageLinks.put(link, imgs[i]);
            items[1 + i] = link;
        }
        return items;
    }

    private void sendText(final String message) {
        if (message.trim().length() == 0) {
            display.setCurrent(chat);
            return;
        }
        final TextBox box = compose;
        final String conv = chatId;
        display.setCurrent(chat);
        enqueue(new Runnable() {
            public void run() {
                call("send\t" + conv, Crypto.utf8(message), null);
                box.setString("");
                loadMessages(true);
            }
        });
    }

    /**
     * Comes to the front with a note if needed, then the sound chosen in the
     * settings and a buzz (none with the soft tone). With the app open and in front just one short buzz
     * (plus the tone, if chosen); woken from outside also light and two buzzes.
     * Vibration only works once the app is on screen, so a helper thread
     * waits for that (up to 5 s).
     */
    private void notifyNew() {
        final boolean wasInFront = inFront();
        if (!wasInFront) {
            Displayable back = display.getCurrent();
            if (back == null || back instanceof Alert) {
                back = chats;
            }
            // No AlertType: the Alert would play its own sound.
            Alert a = new Alert(TITLE, "Neue Nachricht", null, null);
            a.setTimeout(5000);
            display.setCurrent(a, back);
        }
        new Thread(new Runnable() {
            public void run() {
                for (int i = 0; i < 50 && !inFront(); i++) {
                    pause(100);
                }
                if (!wasInFront && !display.flashBacklight(3000)) {
                    try {
                        DeviceControl.setLights(0, 100);
                    } catch (Throwable t) {
                    }
                }
                if (sound == 1) {
                    AlertType.INFO.playSound(display);
                } else if (sound == 2) {
                    AlertType.ALARM.playSound(display);
                }
                int buzzes = sound == 1 ? 0 : wasInFront ? 1 : 2; // soft tone: no buzz
                int ms = wasInFront ? 250 : 500;
                for (int i = 0; i < buzzes; i++) {
                    if (!display.vibrate(ms)) {
                        try {
                            DeviceControl.startVibra(80, ms);
                        } catch (Throwable t) {
                        }
                    }
                    pause(ms + 400);
                }
            }
        }).start();
    }

    private static void pause(long ms) {
        try {
            Thread.sleep(ms);
        } catch (InterruptedException e) {
        }
    }

    // --- images ----------------------------------------------------------

    private void browse(final String path) {
        enqueue(new Runnable() {
            public void run() {
                listFiles(path);
            }
        });
    }

    /** Shows a folder; null = the drives (phone memory, memory card). */
    private void listFiles(String path) {
        Vector names = new Vector();
        if (path == null) {
            Enumeration e = FileSystemRegistry.listRoots();
            while (e.hasMoreElements()) {
                names.addElement(e.nextElement());
            }
        } else {
            names.addElement("..");
            FileConnection fc = null;
            try {
                fc = (FileConnection) Connector.open(path, Connector.READ);
                Enumeration e = fc.list();
                while (e.hasMoreElements()) {
                    String n = (String) e.nextElement();
                    String l = n.toLowerCase();
                    if (n.endsWith("/") || l.endsWith(".jpg") || l.endsWith(".jpeg")
                            || l.endsWith(".png") || l.endsWith(".gif")) {
                        names.addElement(n);
                    }
                }
            } catch (IOException e) {
                throw new RuntimeException("Ordner nicht lesbar");
            } finally {
                closeQuietly(fc);
            }
        }
        filePath = path;
        files = new List(path == null ? "Speicher" : path.substring(8), List.IMPLICIT);
        for (int i = 0; i < names.size(); i++) {
            files.append((String) names.elementAt(i), null);
        }
        files.addCommand(cmdBack);
        files.setCommandListener(this);
        display.setCurrent(files);
    }

    private void fileChosen(String name) {
        if ("..".equals(name)) {
            browse(parent(filePath));
        } else if (name.endsWith("/")) {
            browse((filePath == null ? "file:///" : filePath) + name);
        } else {
            final String url = filePath + name;
            enqueue(new Runnable() {
                public void run() {
                    long size = fileSize(url);
                    sendFile = url;
                    sendPhoto = null;
                    askSend(name(url) + "\n" + (size + 1023) / 1024 + " KB", null);
                }
            });
        }
    }

    private static String name(String url) {
        return url.substring(url.lastIndexOf('/') + 1);
    }

    /** Parent folder, or null at a drive root. */
    private static String parent(String path) {
        String p = path.substring(0, path.length() - 1);
        int i = p.lastIndexOf('/');
        return i <= 8 ? null : p.substring(0, i + 1);
    }

    private long fileSize(String url) {
        FileConnection fc = null;
        try {
            fc = (FileConnection) Connector.open(url, Connector.READ);
            long size = fc.fileSize();
            if (size > MAX_UPLOAD) {
                throw new RuntimeException("Bild ist zu groß (höchstens 3 MB)");
            }
            return size;
        } catch (IOException e) {
            throw new RuntimeException("Datei nicht lesbar");
        } finally {
            closeQuietly(fc);
        }
    }

    private void shoot() {
        CameraView cam = camera;
        if (cam == null) {
            return;
        }
        byte[] jpg = cam.snapshot();
        cam.close();
        camera = null;
        sendFile = null;
        sendPhoto = jpg;
        Image preview = null;
        try {
            preview = Image.createImage(jpg, 0, jpg.length);
        } catch (Throwable t) {
            // Too big to show on the phone; it is sent anyway.
        }
        askSend((jpg.length + 1023) / 1024 + " KB", preview);
    }

    private void askSend(String info, Image preview) {
        confirm = new Form("An " + chatName + " senden?");
        if (preview != null) {
            confirm.append(new ImageItem(null, scaleDown(preview, 200), Item.LAYOUT_CENTER, ""));
        }
        confirm.append(new StringItem(null, info));
        confirm.addCommand(cmdSend);
        confirm.addCommand(cmdCancel);
        confirm.setCommandListener(this);
        display.setCurrent(confirm);
    }

    private void sendImage() {
        final String file = sendFile;
        final byte[] photo = sendPhoto;
        final String conv = chatId;
        sendFile = null;
        sendPhoto = null;
        display.setCurrent(chat);
        enqueue(new Runnable() {
            public void run() {
                call("img\t" + conv, photo, file);
                loadMessages(true);
            }
        });
    }

    /** Nearest-neighbour shrink so a camera preview fits the screen. */
    static Image scaleDown(Image src, int maxW) {
        int w = src.getWidth();
        int h = src.getHeight();
        if (w <= maxW) {
            return src;
        }
        int nw = maxW;
        int nh = h * maxW / w;
        int[] row = new int[w];
        int[] out = new int[nw * nh];
        for (int y = 0; y < nh; y++) {
            src.getRGB(row, 0, w, 0, y * h / nh, w, 1);
            for (int x = 0; x < nw; x++) {
                out[y * nw + x] = row[x * w / nw];
            }
        }
        return Image.createRGBImage(out, nw, nh, false);
    }

    // --- worker ----------------------------------------------------------

    private void enqueue(Runnable job) {
        synchronized (jobs) {
            jobs.addElement(job);
            jobs.notifyAll();
        }
    }

    /** The worker: runs queued jobs, otherwise refreshes after news. */
    public void run() {
        while (true) {
            Runnable job = null;
            synchronized (jobs) {
                while (jobs.isEmpty() && !changed) {
                    if (missed && inFront()) {
                        missed = false;
                        changed = true; // back in front: catch up on the open chat
                        break;
                    }
                    try {
                        jobs.wait(missed ? 1000 : 0);
                    } catch (InterruptedException e) {
                    }
                }
                if (!jobs.isEmpty()) {
                    job = (Runnable) jobs.elementAt(0);
                    jobs.removeElementAt(0);
                } else {
                    changed = false;
                }
                busy = true;
            }
            quiet = job == null;
            if (quiet) {
                job = poll();
            }
            try {
                runJob(job);
            } finally {
                synchronized (jobs) {
                    busy = false;
                    jobs.notifyAll();
                }
            }
        }
    }

    /** Runs one job; errors of background refreshes only show as "offline". */
    private void runJob(Runnable job) {
        Displayable shown = display.getCurrent();
        String title = shown == null ? null : shown.getTitle();
        if (shown != null && !quiet) {
            shown.setTitle("Lädt …");
        }
        try {
            job.run();
            if (quiet) {
                chats.setTitle(TITLE);
            }
        } catch (Throwable t) {
            if (quiet) {
                chats.setTitle(TITLE + " (offline)");
            } else {
                String msg = t.getMessage();
                error(msg == null ? t.toString() : msg);
            }
        } finally {
            if (shown != null && !quiet) {
                shown.setTitle(title);
            }
        }
    }

    /** False while the app is in the background (red key). */
    private boolean inFront() {
        Displayable d = display.getCurrent();
        return d != null && d.isShown();
    }

    /**
     * Refresh after news: the open chat first (marks it read), then the list.
     * In the background only the list, so nothing counts as read unseen.
     */
    private Runnable poll() {
        final boolean inChat = chat != null && display.getCurrent() == chat && inFront();
        if (chat != null && display.getCurrent() == chat && !inChat) {
            synchronized (jobs) {
                missed = true;
            }
        }
        return new Runnable() {
            public void run() {
                if (inChat) {
                    loadMessages(true);
                }
                loadChats();
            }
        };
    }

    /**
     * Second thread: asks the bridge to answer as soon as something changes
     * (at most 25 s later, the bridge's limit), so new messages show up
     * within a few seconds. The answer after 25 s doubles as keepalive, so
     * the mobile network never drops an idle connection. With an interval
     * set it only asks every so often, or not at all ("nur von Hand").
     */
    private void watch() {
        boolean offline = false;
        while (true) {
            if (System.currentTimeMillis() - wakeArmed > 60000) {
                armWake(); // the alarm must not go off while the app is open
            }
            try {
                synchronized (jobs) {
                    // Start a long poll only while the worker is idle; a job that
                    // comes later runs on its own connection next to it.
                    while (crypto == null || busy || changed || !jobs.isEmpty()) {
                        jobs.wait(2000);
                    }
                }
                int mode = pollSeconds;
                if (mode < 0) {
                    pause(2000); // by hand: only "Aktualisieren" fetches
                    continue;
                }
                int v = Integer.parseInt(text(call("wait\t" + seenVersion + "\t" + (mode == 0 ? 25 : 0),
                        null, null)).trim());
                if (!inFront()) {
                    lastBackground = System.currentTimeMillis();
                }
                if (offline) {
                    offline = false;
                    chats.setTitle(TITLE);
                }
                if (v != seenVersion) {
                    boolean first = seenVersion < 0;
                    seenVersion = v;
                    if (!first) {
                        synchronized (jobs) {
                            changed = true;
                            jobs.notifyAll();
                        }
                    }
                }
                // Interval mode: wait, but follow a changed setting at once.
                for (int i = 0; mode > 0 && i < mode && pollSeconds == mode; i++) {
                    pause(1000);
                }
            } catch (Throwable t) {
                offline = true;
                chats.setTitle(TITLE + " (offline)");
                try {
                    Thread.sleep(10000);
                } catch (InterruptedException e) {
                }
            }
        }
    }

    // --- network ---------------------------------------------------------

    /**
     * Sends one encrypted request and returns the answer after "OK\n".
     * The payload comes from bytes or from a file URL (streamed, so big
     * pictures do not have to fit into memory). Throws with the bridge's
     * error message.
     */
    private byte[] call(String header, byte[] payload, String file) {
        for (int attempt = 0;; attempt++) {
            byte[] plain;
            try {
                plain = exchange(header, payload, file);
            } catch (SecurityException e) {
                throw new RuntimeException("Zugriff nicht erlaubt");
            } catch (IOException e) {
                boolean safe = !header.startsWith("send\t") && !header.startsWith("img\t");
                if (safe && attempt == 0) {
                    continue;
                }
                throw new RuntimeException("Keine Verbindung: " + e.getMessage());
            }
            int nl = 0;
            while (nl < plain.length && plain[nl] != '\n') {
                nl++;
            }
            String status = text(plain, 0, nl);
            if ("OK".equals(status)) {
                int start = Math.min(nl + 1, plain.length);
                byte[] body = new byte[plain.length - start];
                System.arraycopy(plain, start, body, 0, body.length);
                return body;
            }
            String[] f = split(status, '\t');
            if (f.length >= 3 && "ZEIT".equals(f[1]) && attempt == 0) {
                offset = Long.parseLong(f[2]) - System.currentTimeMillis();
                continue;
            }
            throw new RuntimeException(f.length >= 2 ? f[1] : status);
        }
    }

    private byte[] exchange(String header, byte[] payload, String file) throws IOException {
        HttpConnection hc = null;
        FileConnection fc = null;
        InputStream src = null;
        OutputStream out = null;
        InputStream in = null;
        try {
            int len = 0;
            if (file != null) {
                fc = (FileConnection) Connector.open(file, Connector.READ);
                len = (int) fc.fileSize();
                src = fc.openInputStream();
            } else if (payload != null) {
                len = payload.length;
                src = new ByteArrayInputStream(payload);
            }
            byte[] h = Crypto.utf8((System.currentTimeMillis() + offset) + "\t" + header);
            byte[] head = new byte[4 + h.length];
            head[0] = (byte) (h.length >>> 24);
            head[1] = (byte) (h.length >>> 16);
            head[2] = (byte) (h.length >>> 8);
            head[3] = (byte) h.length;
            System.arraycopy(h, 0, head, 4, h.length);

            byte[] nonce = crypto.nonce();
            Crypto.ChaCha enc = new Crypto.ChaCha(crypto.upEnc, nonce);
            Crypto.Hmac mac = new Crypto.Hmac(crypto.upMac);

            hc = (HttpConnection) Connector.open(baseUrl + "/x");
            hc.setRequestMethod(HttpConnection.POST);
            hc.setRequestProperty("Content-Type", "application/octet-stream");
            // A fresh connection per request: no answer can end up on the wrong one.
            hc.setRequestProperty("Connection", "close");
            hc.setRequestProperty("Content-Length", String.valueOf(12 + head.length + len + 16));
            out = hc.openOutputStream();
            out.write(nonce);
            mac.update(nonce, 0, 12);
            enc.crypt(head, 0, head.length);
            mac.update(head, 0, head.length);
            out.write(head);
            if (src != null) {
                byte[] buf = new byte[2048];
                int left = len;
                while (left > 0) {
                    int n = src.read(buf, 0, Math.min(buf.length, left));
                    if (n <= 0) {
                        throw new IOException("Datei unvollständig");
                    }
                    enc.crypt(buf, 0, n);
                    mac.update(buf, 0, n);
                    out.write(buf, 0, n);
                    left -= n;
                }
            }
            out.write(mac.finish());

            int code = hc.getResponseCode();
            long expected = hc.getLength();
            in = hc.openInputStream();
            ByteArrayOutputStream body = new ByteArrayOutputStream();
            byte[] buf = new byte[1024];
            int n;
            while ((n = in.read(buf)) != -1) {
                body.write(buf, 0, n);
                if (expected > 0 && body.size() >= expected) {
                    break;
                }
            }
            if (code == 403) {
                throw new RuntimeException("Schlüssel falsch");
            }
            if (code != 200) {
                throw new RuntimeException("HTTP " + code);
            }
            byte[] plain = crypto.open(body.toByteArray());
            if (plain == null) {
                // Usually a cut-off answer; call() asks again where that is safe.
                throw new IOException("Antwort beschädigt (" + body.size() + " von " + expected + " Byte)");
            }
            return plain;
        } finally {
            try {
                if (src != null) {
                    src.close();
                }
                if (out != null) {
                    out.close();
                }
                if (in != null) {
                    in.close();
                }
                if (hc != null) {
                    hc.close();
                }
            } catch (IOException e) {
            }
            closeQuietly(fc);
        }
    }

    private void error(String msg) {
        Displayable next = display.getCurrent();
        if (next == null || next instanceof Alert || next == camera) {
            next = chat != null ? (Displayable) chat : chats;
        }
        Alert a = new Alert("Fehler", msg, null, AlertType.ERROR);
        a.setTimeout(Alert.FOREVER);
        display.setCurrent(a, next);
    }

    // --- settings --------------------------------------------------------

    private void loadSettings() {
        try {
            RecordStore rs = RecordStore.openRecordStore(STORE, true);
            if (rs.getNumRecords() >= 2) {
                baseUrl = new String(rs.getRecord(1), "UTF-8");
                key = new String(rs.getRecord(2), "UTF-8");
            }
            if (rs.getNumRecords() >= 3) {
                pollSeconds = Integer.parseInt(new String(rs.getRecord(3), "UTF-8"));
            }
            if (rs.getNumRecords() >= 4) {
                sound = Integer.parseInt(new String(rs.getRecord(4), "UTF-8"));
            }
            rs.closeRecordStore();
        } catch (Exception e) {
        }
        // Defaults baked into the JAR by build.sh, so nothing has to be typed.
        if (key.length() == 0) {
            String u = getAppProperty("SG-Url");
            String k = getAppProperty("SG-Key");
            if (u != null) {
                baseUrl = trimSlash(u.trim());
            }
            if (k != null) {
                key = k.trim();
            }
        }
    }

    private void saveSettings() {
        try {
            RecordStore.deleteRecordStore(STORE);
        } catch (Exception e) {
        }
        try {
            RecordStore rs = RecordStore.openRecordStore(STORE, true);
            byte[] u = Crypto.utf8(baseUrl);
            byte[] k = Crypto.utf8(key);
            rs.addRecord(u, 0, u.length);
            rs.addRecord(k, 0, k.length);
            byte[] p = Crypto.utf8(String.valueOf(pollSeconds));
            rs.addRecord(p, 0, p.length);
            byte[] t = Crypto.utf8(String.valueOf(sound));
            rs.addRecord(t, 0, t.length);
            rs.closeRecordStore();
        } catch (Exception e) {
            error("Speichern fehlgeschlagen");
        }
    }

    // --- wake-up alarm ----------------------------------------------------

    /** Record store "wecker2" (1.0.18: new name, so the alarm starts off): one record "alarm ms \t minutes \t last result". */
    private void loadWake() {
        String[] f = cacheRead("wecker2", "wecker");
        try {
            if (f != null && f.length >= 1) {
                String[] w = split(f[0], '\t');
                wakeAt = Long.parseLong(w[0]);
                wakeMinutes = Integer.parseInt(w[1]);
                wakeLog = w.length >= 3 ? w[2] : "";
            }
        } catch (Exception e) {
        }
    }

    /** Lets the phone start the app wakeMinutes from now (or never). */
    private synchronized void armWake() {
        long now = System.currentTimeMillis();
        long at = wakeMinutes > 0 ? now + wakeMinutes * 60000L : 0;
        wakeArmed = now;
        if (at == 0 && wakeAt == 0) {
            return; // alarm off and none set: do not touch PushRegistry (Autostart right)
        }
        try {
            PushRegistry.registerAlarm(getClass().getName(), at);
            wakeAt = at;
        } catch (Throwable t) {
            wakeAt = 0;
            wakeLog = clock(now) + " Wecker abgelehnt: " + t;
        }
        wakeArmed = now;
        cacheWrite("wecker2", "wecker", new String[] {wakeAt + "\t" + wakeMinutes + "\t" + wakeLog}, 0);
    }

    /** HH:MM in the phone's time zone. */
    static String clock(long ms) {
        Calendar c = Calendar.getInstance();
        c.setTime(new java.util.Date(ms));
        int h = c.get(Calendar.HOUR_OF_DAY);
        int m = c.get(Calendar.MINUTE);
        return (h < 10 ? "0" : "") + h + ":" + (m < 10 ? "0" : "") + m;
    }

    // --- cache (record stores in the phone memory) ------------------------

    /** One record store per chat; names may only be 32 characters long. */
    static String cacheName(String conv) {
        return "m" + Integer.toHexString(conv.hashCode());
    }

    /** The cached lines, or null; the first line must name the chat. */
    static String[] cacheRead(String name, String conv) {
        RecordStore rs = null;
        try {
            rs = RecordStore.openRecordStore(name, false);
            if (rs.getNumRecords() < 1) {
                return null;
            }
            String[] lines = split(text(rs.getRecord(1)), '\n');
            if (lines.length == 0 || !lines[0].equals(conv)) {
                return null;
            }
            String[] out = new String[lines.length - 1];
            System.arraycopy(lines, 1, out, 0, out.length);
            return out;
        } catch (Exception e) {
            return null;
        } finally {
            closeQuietly(rs);
        }
    }

    /** Stores the last max lines (all if max is 0). */
    static void cacheWrite(String name, String conv, String[] lines, int max) {
        Vector v = new Vector();
        for (int i = 0; i < lines.length; i++) {
            v.addElement(lines[i]);
        }
        cacheWrite(name, conv, v, max);
    }

    static void cacheWrite(String name, String conv, Vector lines, int max) {
        StringBuffer sb = new StringBuffer(conv);
        int from = max > 0 ? Math.max(0, lines.size() - max) : 0;
        for (int i = from; i < lines.size(); i++) {
            sb.append('\n').append((String) lines.elementAt(i));
        }
        byte[] data = Crypto.utf8(sb.toString());
        RecordStore rs = null;
        try {
            rs = RecordStore.openRecordStore(name, true);
            if (rs.getNumRecords() < 1) {
                rs.addRecord(data, 0, data.length);
            } else {
                rs.setRecord(1, data, 0, data.length);
            }
        } catch (Exception e) {
            // Phone memory full: it just loads from the bridge next time.
        } finally {
            closeQuietly(rs);
        }
    }

    static void closeQuietly(RecordStore rs) {
        try {
            if (rs != null) {
                rs.closeRecordStore();
            }
        } catch (Exception e) {
        }
    }

    // --- helpers ---------------------------------------------------------

    static String text(byte[] b) {
        return text(b, 0, b.length);
    }

    static String text(byte[] b, int off, int len) {
        try {
            return new String(b, off, len, "UTF-8");
        } catch (Exception e) {
            return new String(b, off, len);
        }
    }

    static String oneLine(String s) {
        return s.replace('\u001e', ' ');
    }

    static void closeQuietly(FileConnection fc) {
        try {
            if (fc != null) {
                fc.close();
            }
        } catch (IOException e) {
        }
    }

    static String[] split(String s, char sep) {
        Vector parts = new Vector();
        int start = 0;
        for (int i = 0; i <= s.length(); i++) {
            if (i == s.length() || s.charAt(i) == sep) {
                parts.addElement(s.substring(start, i));
                start = i + 1;
            }
        }
        // Drop a trailing empty line, but keep empty fields in between.
        if (sep == '\n' && parts.size() > 0 && ((String) parts.lastElement()).length() == 0) {
            parts.removeElementAt(parts.size() - 1);
        }
        String[] out = new String[parts.size()];
        parts.copyInto(out);
        return out;
    }

    static String trimSlash(String s) {
        while (s.endsWith("/")) {
            s = s.substring(0, s.length() - 1);
        }
        return s;
    }

    // --- picture viewer --------------------------------------------------

    /** Shows one picture; Zoom/5 doubles the size, the arrow keys move it. */
    class ImageView extends Canvas {
        private final String att;
        private Image img;
        private int scale = 1;
        private int x;
        private int y;
        private String msg = "Lädt …";

        ImageView(String att) {
            this.att = att;
            setTitle("Bild");
            addCommand(cmdZoom);
            addCommand(cmdBack);
            setCommandListener(Signal.this);
        }

        void load() {
            final int w = Math.min(1024, getWidth() * scale);
            final int h = Math.min(1024, getHeight() * scale);
            msg = "Lädt …";
            repaint();
            enqueue(new Runnable() {
                public void run() {
                    try {
                        byte[] jpg = call("att\t" + att + "\t" + w + "\t" + h, null, null);
                        img = null;
                        img = Image.createImage(jpg, 0, jpg.length);
                        x = Math.max(0, (img.getWidth() - getWidth()) / 2);
                        y = Math.max(0, (img.getHeight() - getHeight()) / 2);
                        msg = null;
                    } catch (RuntimeException e) {
                        msg = e.getMessage();
                    } catch (OutOfMemoryError e) {
                        img = null;
                        msg = "Zu wenig Speicher";
                    }
                    repaint();
                }
            });
        }

        void zoom() {
            scale = scale == 1 ? 2 : 1;
            load();
        }

        protected void paint(Graphics g) {
            g.setColor(0x000000);
            g.fillRect(0, 0, getWidth(), getHeight());
            if (img != null) {
                int ox = img.getWidth() < getWidth() ? (getWidth() - img.getWidth()) / 2 : -x;
                int oy = img.getHeight() < getHeight() ? (getHeight() - img.getHeight()) / 2 : -y;
                g.drawImage(img, ox, oy, Graphics.TOP | Graphics.LEFT);
            }
            if (msg != null) {
                g.setColor(0xffffff);
                g.setFont(Font.getDefaultFont());
                g.drawString(msg, getWidth() / 2, getHeight() / 2, Graphics.TOP | Graphics.HCENTER);
            }
        }

        protected void keyPressed(int code) {
            if (img == null) {
                return;
            }
            int a = getGameAction(code);
            int step = 48;
            if (a == FIRE) {
                zoom();
                return;
            } else if (a == LEFT) {
                x -= step;
            } else if (a == RIGHT) {
                x += step;
            } else if (a == UP) {
                y -= step;
            } else if (a == DOWN) {
                y += step;
            }
            x = Math.max(0, Math.min(x, img.getWidth() - getWidth()));
            y = Math.max(0, Math.min(y, img.getHeight() - getHeight()));
            repaint();
        }

        protected void keyRepeated(int code) {
            keyPressed(code);
        }
    }

    // --- camera ----------------------------------------------------------

    /** Live camera picture; Auslösen/5 takes the photo. */
    class CameraView extends Canvas {
        private Player player;
        private VideoControl video;
        private String msg = "Kamera startet …";

        CameraView() {
            setTitle("Foto");
            addCommand(cmdShoot);
            addCommand(cmdCancel);
            setCommandListener(Signal.this);
        }

        /** Runs on the worker thread because the phone may ask for permission. */
        void open() {
            try {
                // Newer Series 40 phones only take photos via capture://image.
                try {
                    player = Manager.createPlayer("capture://image");
                } catch (MediaException e) {
                    player = Manager.createPlayer("capture://video");
                }
                player.realize();
                video = (VideoControl) player.getControl("VideoControl");
                video.initDisplayMode(VideoControl.USE_DIRECT_VIDEO, this);
                try {
                    video.setDisplayFullScreen(true);
                } catch (MediaException e) {
                    // keep the default size
                }
                video.setVisible(true);
                player.start();
                msg = null;
            } catch (Exception e) {
                close();
                msg = "Kamera geht nicht: " + e.getMessage();
            }
            repaint();
        }

        byte[] snapshot() {
            if (video == null) {
                throw new RuntimeException(msg != null ? msg : "Kamera nicht bereit");
            }
            // 640x480 keeps the upload small; not every phone supports the size.
            String[] modes = { "encoding=jpeg&width=640&height=480", "encoding=jpeg", null };
            for (int i = 0; i < modes.length; i++) {
                try {
                    return video.getSnapshot(modes[i]);
                } catch (MediaException e) {
                    if (i == modes.length - 1) {
                        throw new RuntimeException("Foto fehlgeschlagen: " + e.getMessage());
                    }
                }
            }
            return null;
        }

        void close() {
            if (player != null) {
                player.close();
                player = null;
                video = null;
            }
        }

        protected void paint(Graphics g) {
            g.setColor(0x000000);
            g.fillRect(0, 0, getWidth(), getHeight());
            if (msg != null) {
                g.setColor(0xffffff);
                g.drawString(msg, getWidth() / 2, getHeight() / 2, Graphics.TOP | Graphics.HCENTER);
            }
        }

        protected void keyPressed(int code) {
            if (getGameAction(code) == FIRE) {
                commandAction(cmdShoot, this);
            }
        }
    }
}
