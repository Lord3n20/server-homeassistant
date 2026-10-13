import java.util.Random;

/**
 * ChaCha20 (RFC 8439) and HMAC-SHA256 for the bridge protocol, written for
 * CLDC 1.1 because the phone has no usable crypto API.
 *
 * Frame: nonce (12) | ChaCha20(plain) | HMAC-SHA256(nonce | ciphertext)[0..16]
 */
final class Crypto {
    final byte[] upEnc;
    final byte[] upMac;
    final byte[] downEnc;
    final byte[] downMac;
    private final Random random;

    Crypto(String key) {
        upEnc = derive("up-enc", key);
        upMac = derive("up-mac", key);
        downEnc = derive("down-enc", key);
        downMac = derive("down-mac", key);
        random = new Random(System.currentTimeMillis() ^ Runtime.getRuntime().freeMemory()
                ^ ((long) key.hashCode() << 32));
    }

    private static byte[] derive(String label, String key) {
        Sha256 h = new Sha256();
        byte[] b = utf8(label + "\n" + key);
        h.update(b, 0, b.length);
        return h.digest();
    }

    static byte[] utf8(String s) {
        try {
            return s.getBytes("UTF-8");
        } catch (Exception e) {
            return s.getBytes();
        }
    }

    /** 6 bytes of the clock plus 6 random bytes, so nonces never repeat. */
    synchronized byte[] nonce() {
        byte[] n = new byte[12];
        long t = System.currentTimeMillis();
        long r = random.nextLong();
        for (int i = 0; i < 6; i++) {
            n[i] = (byte) (t >>> (8 * i));
            n[6 + i] = (byte) (r >>> (8 * i));
        }
        return n;
    }

    /** Checks and decrypts a frame from the bridge; null if it is not genuine. */
    byte[] open(byte[] frame) {
        if (frame.length < 28) {
            return null;
        }
        int len = frame.length - 28;
        Hmac mac = new Hmac(downMac);
        mac.update(frame, 0, 12 + len);
        byte[] tag = mac.finish();
        int diff = 0;
        for (int i = 0; i < 16; i++) {
            diff |= tag[i] ^ frame[12 + len + i];
        }
        if (diff != 0) {
            return null;
        }
        byte[] nonce = new byte[12];
        System.arraycopy(frame, 0, nonce, 0, 12);
        byte[] plain = new byte[len];
        System.arraycopy(frame, 12, plain, 0, len);
        new ChaCha(downEnc, nonce).crypt(plain, 0, len);
        return plain;
    }

    // --- ChaCha20 ----------------------------------------------------------

    static final class ChaCha {
        private final int[] s = new int[16];
        private final int[] x = new int[16];
        private final byte[] ks = new byte[64];
        private int pos = 64;

        ChaCha(byte[] key, byte[] nonce) {
            s[0] = 0x61707865;
            s[1] = 0x3320646e;
            s[2] = 0x79622d32;
            s[3] = 0x6b206574;
            for (int i = 0; i < 8; i++) {
                s[4 + i] = le(key, 4 * i);
            }
            s[12] = 0;
            for (int i = 0; i < 3; i++) {
                s[13 + i] = le(nonce, 4 * i);
            }
        }

        void crypt(byte[] b, int off, int len) {
            for (int i = 0; i < len; i++) {
                if (pos == 64) {
                    block();
                    pos = 0;
                }
                b[off + i] ^= ks[pos++];
            }
        }

        private void block() {
            System.arraycopy(s, 0, x, 0, 16);
            for (int i = 0; i < 10; i++) {
                qr(0, 4, 8, 12);
                qr(1, 5, 9, 13);
                qr(2, 6, 10, 14);
                qr(3, 7, 11, 15);
                qr(0, 5, 10, 15);
                qr(1, 6, 11, 12);
                qr(2, 7, 8, 13);
                qr(3, 4, 9, 14);
            }
            for (int i = 0; i < 16; i++) {
                int v = x[i] + s[i];
                ks[4 * i] = (byte) v;
                ks[4 * i + 1] = (byte) (v >>> 8);
                ks[4 * i + 2] = (byte) (v >>> 16);
                ks[4 * i + 3] = (byte) (v >>> 24);
            }
            s[12]++;
        }

        private void qr(int a, int b, int c, int d) {
            int[] v = x;
            v[a] += v[b];
            v[d] = rotl(v[d] ^ v[a], 16);
            v[c] += v[d];
            v[b] = rotl(v[b] ^ v[c], 12);
            v[a] += v[b];
            v[d] = rotl(v[d] ^ v[a], 8);
            v[c] += v[d];
            v[b] = rotl(v[b] ^ v[c], 7);
        }

        private static int rotl(int v, int n) {
            return (v << n) | (v >>> (32 - n));
        }

        private static int le(byte[] b, int o) {
            return (b[o] & 0xff) | (b[o + 1] & 0xff) << 8 | (b[o + 2] & 0xff) << 16 | (b[o + 3] & 0xff) << 24;
        }
    }

    // --- HMAC-SHA256 -------------------------------------------------------

    static final class Hmac {
        private final Sha256 inner = new Sha256();
        private final byte[] opad = new byte[64];

        /** key must be at most 64 bytes (ours are 32). */
        Hmac(byte[] key) {
            byte[] ipad = new byte[64];
            for (int i = 0; i < 64; i++) {
                byte k = i < key.length ? key[i] : 0;
                ipad[i] = (byte) (k ^ 0x36);
                opad[i] = (byte) (k ^ 0x5c);
            }
            inner.update(ipad, 0, 64);
        }

        void update(byte[] b, int off, int len) {
            inner.update(b, off, len);
        }

        /** First 16 bytes of the MAC. */
        byte[] finish() {
            byte[] ih = inner.digest();
            Sha256 outer = new Sha256();
            outer.update(opad, 0, 64);
            outer.update(ih, 0, 32);
            byte[] full = outer.digest();
            byte[] tag = new byte[16];
            System.arraycopy(full, 0, tag, 0, 16);
            return tag;
        }
    }

    // --- SHA-256 -----------------------------------------------------------

    static final class Sha256 {
        private static final int[] K = {
            0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
            0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
            0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
            0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
            0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
            0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
            0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
            0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2
        };
        private final int[] h = {
            0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19
        };
        private final int[] w = new int[64];
        private final byte[] buf = new byte[64];
        private int fill;
        private long count;

        void update(byte[] b, int off, int len) {
            count += len;
            while (len > 0) {
                int n = Math.min(64 - fill, len);
                System.arraycopy(b, off, buf, fill, n);
                fill += n;
                off += n;
                len -= n;
                if (fill == 64) {
                    compress();
                    fill = 0;
                }
            }
        }

        byte[] digest() {
            long bits = count * 8;
            byte[] pad = new byte[(fill < 56 ? 56 : 120) - fill + 8];
            pad[0] = (byte) 0x80;
            for (int i = 0; i < 8; i++) {
                pad[pad.length - 1 - i] = (byte) (bits >>> (8 * i));
            }
            update(pad, 0, pad.length);
            byte[] out = new byte[32];
            for (int i = 0; i < 8; i++) {
                out[4 * i] = (byte) (h[i] >>> 24);
                out[4 * i + 1] = (byte) (h[i] >>> 16);
                out[4 * i + 2] = (byte) (h[i] >>> 8);
                out[4 * i + 3] = (byte) h[i];
            }
            return out;
        }

        private void compress() {
            for (int i = 0; i < 16; i++) {
                w[i] = (buf[4 * i] & 0xff) << 24 | (buf[4 * i + 1] & 0xff) << 16
                        | (buf[4 * i + 2] & 0xff) << 8 | (buf[4 * i + 3] & 0xff);
            }
            for (int i = 16; i < 64; i++) {
                int a = w[i - 15];
                int b = w[i - 2];
                int s0 = (a >>> 7 | a << 25) ^ (a >>> 18 | a << 14) ^ (a >>> 3);
                int s1 = (b >>> 17 | b << 15) ^ (b >>> 19 | b << 13) ^ (b >>> 10);
                w[i] = w[i - 16] + s0 + w[i - 7] + s1;
            }
            int a = h[0], b = h[1], c = h[2], d = h[3], e = h[4], f = h[5], g = h[6], hh = h[7];
            for (int i = 0; i < 64; i++) {
                int s1 = (e >>> 6 | e << 26) ^ (e >>> 11 | e << 21) ^ (e >>> 25 | e << 7);
                int t1 = hh + s1 + ((e & f) ^ (~e & g)) + K[i] + w[i];
                int s0 = (a >>> 2 | a << 30) ^ (a >>> 13 | a << 19) ^ (a >>> 22 | a << 10);
                int t2 = s0 + ((a & b) ^ (a & c) ^ (b & c));
                hh = g;
                g = f;
                f = e;
                e = d + t1;
                d = c;
                c = b;
                b = a;
                a = t1 + t2;
            }
            h[0] += a;
            h[1] += b;
            h[2] += c;
            h[3] += d;
            h[4] += e;
            h[5] += f;
            h[6] += g;
            h[7] += hh;
        }
    }
}
