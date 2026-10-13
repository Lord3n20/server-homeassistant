import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.util.Vector;

import javax.microedition.io.Connector;
import javax.microedition.io.HttpConnection;
import javax.microedition.lcdui.Alert;
import javax.microedition.lcdui.AlertType;
import javax.microedition.lcdui.Canvas;
import javax.microedition.lcdui.Choice;
import javax.microedition.lcdui.Command;
import javax.microedition.lcdui.CommandListener;
import javax.microedition.lcdui.Display;
import javax.microedition.lcdui.Displayable;
import javax.microedition.lcdui.Font;
import javax.microedition.lcdui.Form;
import javax.microedition.lcdui.Graphics;
import javax.microedition.lcdui.List;
import javax.microedition.lcdui.StringItem;
import javax.microedition.lcdui.TextField;
import javax.microedition.midlet.MIDlet;
import javax.microedition.rms.RecordStore;

/**
 * Shows the Home Assistant dashboard through the Nokia bridge app
 * (plain HTTP, tab-separated text). Written for CLDC 1.1 / MIDP 2.0,
 * so Java 1.3 syntax only: no generics, no foreach, no String.split.
 */
public class HomeAssistant extends MIDlet implements CommandListener {
    private static final String STORE = "ha";
    private static final String TITLE = "Home Assistant";

    private static final String[] MEDIA_LABELS = { "An/Aus", "Zurück", "Play/Pause", "Weiter" };
    private static final String[] MEDIA_CMDS = { "power", "prev", "play", "next" };
    private static final String[] LIGHT_LABELS = { "Umschalten", "10 %", "25 %", "50 %", "75 %", "100 %", "Aus" };
    private static final String[] LIGHT_VALUES = { null, "10", "25", "50", "75", "100", "0" };

    private final Command cmdRefresh = new Command("Aktualisieren", Command.SCREEN, 1);
    private final Command cmdSettings = new Command("Einstellungen", Command.SCREEN, 2);
    private final Command cmdExit = new Command("Beenden", Command.EXIT, 3);
    private final Command cmdSave = new Command("Speichern", Command.OK, 1);
    private final Command cmdBack = new Command("Zurück", Command.BACK, 2);

    private Display display;
    private List main;
    private List choice;
    private Form settings;
    private TextField fieldUrl;
    private TextField fieldKey;

    private String baseUrl = "";
    private String key = "";

    /** One String[] {index, kind, name, state, options} per line of /s. */
    private Vector items = new Vector();
    /** Item that the open choice list belongs to. */
    private String[] current;
    private boolean busy;

    public void startApp() {
        if (display != null) {
            return;
        }
        display = Display.getDisplay(this);
        main = new List(TITLE, List.IMPLICIT);
        main.setFitPolicy(Choice.TEXT_WRAP_ON);
        main.addCommand(cmdRefresh);
        main.addCommand(cmdSettings);
        main.addCommand(cmdExit);
        main.setCommandListener(this);
        display.setCurrent(main);

        loadSettings();
        if (key.length() == 0) {
            showSettings();
        } else {
            refresh();
        }
    }

    public void pauseApp() {
    }

    public void destroyApp(boolean unconditional) {
    }

    // --- commands --------------------------------------------------------

    public void commandAction(Command c, Displayable d) {
        if (c == cmdExit) {
            notifyDestroyed();
        } else if (c == cmdRefresh) {
            refresh();
        } else if (c == cmdSettings) {
            showSettings();
        } else if (c == cmdSave) {
            baseUrl = trimSlash(fieldUrl.getString().trim());
            key = fieldKey.getString().trim();
            saveSettings();
            display.setCurrent(main);
            refresh();
        } else if (c == cmdBack) {
            display.setCurrent(main);
        } else if (c == List.SELECT_COMMAND && d == main) {
            int i = main.getSelectedIndex();
            if (i >= 0 && i < items.size()) {
                open((String[]) items.elementAt(i));
            }
        } else if (c == List.SELECT_COMMAND && d == choice) {
            chosen(choice.getSelectedIndex());
        }
    }

    /** What happens when an entry of the main list is selected. */
    private void open(String[] it) {
        char kind = it[1].charAt(0);
        current = it;
        if (kind == 'T') {
            action(it[0], "toggle", null);
        } else if (kind == 'B') {
            action(it[0], "press", null);
        } else if (kind == 'S') {
            showChoice(it[2], split(it[4], ';'));
        } else if (kind == 'L') {
            showChoice(it[2], LIGHT_LABELS);
        } else if (kind == 'M') {
            showChoice(it[2], MEDIA_LABELS);
        } else if (kind == 'W') {
            weather();
        } else if (kind == 'G') {
            graph(it);
        }
    }

    private void chosen(int i) {
        if (i < 0 || current == null) {
            return;
        }
        char kind = current[1].charAt(0);
        display.setCurrent(main);
        if (kind == 'S') {
            action(current[0], "select", choice.getString(i));
        } else if (kind == 'L') {
            if (LIGHT_VALUES[i] == null) {
                action(current[0], "toggle", null);
            } else {
                action(current[0], "bright", LIGHT_VALUES[i]);
            }
        } else if (kind == 'M') {
            action(current[0], MEDIA_CMDS[i], null);
        }
    }

    private void showChoice(String title, String[] labels) {
        choice = new List(title, List.IMPLICIT, labels, null);
        choice.addCommand(cmdBack);
        choice.setCommandListener(this);
        display.setCurrent(choice);
    }

    private void showSettings() {
        settings = new Form("Einstellungen");
        fieldUrl = new TextField("Adresse", baseUrl, 100, TextField.URL);
        fieldKey = new TextField("Schlüssel", key, 64, TextField.NON_PREDICTIVE);
        settings.append(fieldUrl);
        settings.append(fieldKey);
        settings.addCommand(cmdSave);
        settings.addCommand(cmdBack);
        settings.setCommandListener(this);
        display.setCurrent(settings);
    }

    // --- network (always off the UI thread) ------------------------------

    private void refresh() {
        run(new Runnable() {
            public void run() {
                loadStates();
            }
        });
    }

    /** Fetches /s and rebuilds the main list; runs on the network thread. */
    private void loadStates() {
        String[] lines = request("/s");
        if (lines == null) {
            return;
        }
        Vector list = new Vector();
        for (int i = 1; i < lines.length; i++) {
            String[] f = split(lines[i], '\t');
            if (f.length >= 5) {
                list.addElement(f);
            }
        }
        int selected = main.getSelectedIndex();
        items = list;
        main.deleteAll();
        for (int i = 0; i < list.size(); i++) {
            String[] f = (String[]) list.elementAt(i);
            if ("H".equals(f[1])) {
                main.append("\u2014 " + f[2] + " \u2014", null);
            } else {
                main.append(f[2] + ": " + f[3], null);
            }
        }
        if (selected >= 0 && selected < main.size()) {
            main.setSelectedIndex(selected, true);
        }
        main.setTitle(TITLE);
    }

    private void action(final String index, final String cmd, final String value) {
        run(new Runnable() {
            public void run() {
                String path = "/a?i=" + index + "&c=" + cmd;
                if (value != null) {
                    path += "&v=" + urlEncode(value);
                }
                if (request(path) == null) {
                    return;
                }
                // Give Home Assistant a moment to update the state.
                try {
                    Thread.sleep(700);
                } catch (InterruptedException e) {
                }
                loadStates();
            }
        });
    }

    private void weather() {
        run(new Runnable() {
            public void run() {
                String[] lines = request("/w");
                if (lines == null) {
                    return;
                }
                Form f = new Form("Wetter");
                f.append(new StringItem("Jetzt", current[3]));
                for (int i = 1; i < lines.length; i++) {
                    String[] d = split(lines[i], '\t');
                    if (d.length >= 4) {
                        f.append(new StringItem(d[0], d[1] + "° / " + d[2] + "°  " + d[3]));
                    }
                }
                f.addCommand(cmdBack);
                f.setCommandListener(HomeAssistant.this);
                main.setTitle(TITLE);
                display.setCurrent(f);
            }
        });
    }

    private void graph(final String[] it) {
        run(new Runnable() {
            public void run() {
                String[] lines = request("/g?i=" + it[0]);
                if (lines == null || lines.length < 2) {
                    return;
                }
                String[] parts = split(lines[1], ',');
                int[] values = new int[parts.length];
                for (int i = 0; i < parts.length; i++) {
                    values[i] = Integer.parseInt(parts[i]);
                }
                Graph g = new Graph(it[2], it[3], values);
                g.addCommand(cmdBack);
                g.setCommandListener(HomeAssistant.this);
                main.setTitle(TITLE);
                display.setCurrent(g);
            }
        });
    }

    /** Runs a network job in its own thread, one at a time. */
    private void run(final Runnable job) {
        synchronized (this) {
            if (busy) {
                return;
            }
            busy = true;
        }
        main.setTitle("Lädt …");
        new Thread(new Runnable() {
            public void run() {
                try {
                    job.run();
                } catch (Exception e) {
                    error("Fehler: " + e.toString());
                } finally {
                    busy = false;
                }
            }
        }).start();
    }

    /**
     * GET baseUrl + path (+ key), returns the lines or null after showing
     * the error. The first line is "OK" or "ERR\tmessage".
     */
    private String[] request(String path) {
        HttpConnection hc = null;
        InputStream in = null;
        try {
            String url = baseUrl + path + (path.indexOf('?') < 0 ? "?" : "&") + "k=" + urlEncode(key);
            hc = (HttpConnection) Connector.open(url);
            hc.setRequestMethod(HttpConnection.GET);
            int code = hc.getResponseCode();
            in = hc.openInputStream();
            ByteArrayOutputStream out = new ByteArrayOutputStream();
            byte[] buf = new byte[512];
            int n;
            while ((n = in.read(buf)) > 0) {
                out.write(buf, 0, n);
            }
            String[] lines = split(new String(out.toByteArray(), "UTF-8"), '\n');
            if (lines.length == 0 || !"OK".equals(lines[0])) {
                String msg = "HTTP " + code;
                if (lines.length > 0 && lines[0].startsWith("ERR\t")) {
                    msg = lines[0].substring(4);
                }
                error(msg);
                return null;
            }
            return lines;
        } catch (Exception e) {
            error("Keine Verbindung: " + e.getMessage());
            return null;
        } finally {
            try {
                if (in != null) {
                    in.close();
                }
                if (hc != null) {
                    hc.close();
                }
            } catch (Exception e) {
            }
        }
    }

    private void error(String msg) {
        main.setTitle(TITLE);
        Alert a = new Alert("Fehler", msg, null, AlertType.ERROR);
        a.setTimeout(Alert.FOREVER);
        display.setCurrent(a, main);
    }

    // --- settings --------------------------------------------------------

    private void loadSettings() {
        try {
            RecordStore rs = RecordStore.openRecordStore(STORE, true);
            if (rs.getNumRecords() >= 2) {
                baseUrl = new String(rs.getRecord(1), "UTF-8");
                key = new String(rs.getRecord(2), "UTF-8");
            }
            rs.closeRecordStore();
        } catch (Exception e) {
        }
        // Defaults baked into the JAR by build.sh, so nothing has to be typed.
        if (key.length() == 0) {
            String u = getAppProperty("HA-Url");
            String k = getAppProperty("HA-Key");
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
            byte[] u = baseUrl.getBytes("UTF-8");
            byte[] k = key.getBytes("UTF-8");
            rs.addRecord(u, 0, u.length);
            rs.addRecord(k, 0, k.length);
            rs.closeRecordStore();
        } catch (Exception e) {
            error("Speichern fehlgeschlagen");
        }
    }

    // --- helpers ---------------------------------------------------------

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

    static String urlEncode(String s) {
        StringBuffer sb = new StringBuffer();
        byte[] b;
        try {
            b = s.getBytes("UTF-8");
        } catch (Exception e) {
            b = s.getBytes();
        }
        String hex = "0123456789ABCDEF";
        for (int i = 0; i < b.length; i++) {
            int c = b[i] & 0xff;
            if ((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9')
                    || c == '-' || c == '_' || c == '.') {
                sb.append((char) c);
            } else {
                sb.append('%').append(hex.charAt(c >> 4)).append(hex.charAt(c & 15));
            }
        }
        return sb.toString();
    }

    static String trimSlash(String s) {
        while (s.endsWith("/")) {
            s = s.substring(0, s.length() - 1);
        }
        return s;
    }

    /** 24 h line graph; values are tenths (e.g. 250 = 25,0). */
    static class Graph extends Canvas {
        private final String name;
        private final String now;
        private final int[] v;

        Graph(String name, String now, int[] values) {
            this.name = name;
            this.now = now;
            this.v = values;
        }

        protected void paint(Graphics g) {
            int w = getWidth();
            int h = getHeight();
            Font f = Font.getFont(Font.FACE_SYSTEM, Font.STYLE_PLAIN, Font.SIZE_SMALL);
            int fh = f.getHeight();
            g.setFont(f);
            g.setColor(0x111111);
            g.fillRect(0, 0, w, h);

            int min = v[0];
            int max = v[0];
            for (int i = 1; i < v.length; i++) {
                if (v[i] < min) {
                    min = v[i];
                }
                if (v[i] > max) {
                    max = v[i];
                }
            }
            if (max - min < 10) {
                max = min + 10;
            }

            g.setColor(0xffffff);
            g.drawString(name, 2, 2, Graphics.TOP | Graphics.LEFT);
            g.drawString("Jetzt " + now, 2, 2 + fh, Graphics.TOP | Graphics.LEFT);

            int top = 4 + 3 * fh;
            int bottom = h - fh - 4;
            int left = 4;
            int right = w - 4;

            g.setColor(0x555555);
            g.drawLine(left, top, right, top);
            g.drawLine(left, bottom, right, bottom);
            g.setColor(0xaaaaaa);
            g.drawString(tenths(max), left, top - 1, Graphics.BOTTOM | Graphics.LEFT);
            g.drawString(tenths(min), left, bottom + 1, Graphics.TOP | Graphics.LEFT);
            g.drawString("24 h", right, bottom + 1, Graphics.TOP | Graphics.RIGHT);

            g.setColor(0x03a9f4);
            int px = 0;
            int py = 0;
            for (int i = 0; i < v.length; i++) {
                int x = left + (right - left) * i / (v.length - 1);
                int y = bottom - (bottom - top) * (v[i] - min) / (max - min);
                if (i > 0) {
                    g.drawLine(px, py, x, y);
                    g.drawLine(px, py + 1, x, y + 1);
                }
                px = x;
                py = y;
            }
        }

        private static String tenths(int t) {
            String sign = t < 0 ? "-" : "";
            t = Math.abs(t);
            return sign + (t / 10) + "," + (t % 10);
        }
    }
}
