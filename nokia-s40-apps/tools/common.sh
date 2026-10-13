# Shared part of the build.sh scripts, included with "source".
#
# build_app NAME PREFIX "extra jars" "extra manifest lines" sign(yes|no)
#   compiles src/*.java (CLDC 1.1 / MIDP 2.0) into dist/NAME.jar + dist/NAME.jad.
#   URL and key from ../private/<app>-url.txt and <app>-key.txt are baked in as
#   PREFIX-Url / PREFIX-Key, so nothing has to be typed on the phone.
#   Signs with ../private/certificate/ (certificate.pem + key.pem) if present.
#
# ecj -target cldc1.1 writes class version 45.3 including StackMap, so no preverify is needed.
# The MIDP/CLDC APIs come from MicroEmulator (Arch: AUR package microemulator).

set -e
T="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
P="$T/../private"
L=${MICROEMU_LIB:-/usr/share/java/microemulator/lib}
ECJ_URL=https://repo1.maven.org/maven2/org/eclipse/jdt/core/compiler/ecj/4.5.1/ecj-4.5.1.jar
ECJ_SHA256=19f313fb13191477e7c7e0f1142f096fef19b721979efc7f18cea81d259e7294

ecj() {
    if [ ! -s "$T/ecj.jar" ]; then
        echo "downloading ecj 4.5.1 …" >&2
        curl -fsSL -o "$T/ecj.jar.tmp" "$ECJ_URL"
        echo "$ECJ_SHA256  $T/ecj.jar.tmp" | sha256sum -c --quiet -
        mv "$T/ecj.jar.tmp" "$T/ecj.jar"
    fi
    java -jar "$T/ecj.jar" "$@"
}

build_app() {
    local name=$1 prefix=$2 extra=$3 lines=$4 sign=$5
    local app
    app=$(basename "$PWD")
    rm -rf build dist && mkdir build dist
    ecj -source 1.3 -target cldc1.1 \
        -bootclasspath "$L/cldcapi11.jar:$L/midpapi20.jar$extra" \
        -d build src/*.java

    { cat src/manifest.txt
      [ -n "$lines" ] && echo "$lines"
      [ -s "$P/$app-url.txt" ] && echo "$prefix-Url: $(tr -d '[:space:]' < "$P/$app-url.txt")"
      [ -s "$P/$app-key.txt" ] && echo "$prefix-Key: $(tr -d '[:space:]' < "$P/$app-key.txt")"
      true
    } > dist/manifest.mf
    jar cfm "dist/$name.jar" dist/manifest.mf -C build . -C res icon.png
    { cat dist/manifest.mf
      echo "MIDlet-Jar-URL: $name.jar"
      echo "MIDlet-Jar-Size: $(stat -c %s "dist/$name.jar")"
    } > "dist/$name.jad"
    rm dist/manifest.mf

    local c="$P/certificate"
    if [ "$sign" = yes ] && [ -s "$c/certificate.pem" ] && [ -s "$c/key.pem" ]; then
        echo "MIDlet-Certificate-1-1: $(openssl x509 -in "$c/certificate.pem" -outform DER | base64 -w0)" >> "dist/$name.jad"
        echo "MIDlet-Jar-RSA-SHA1: $(openssl dgst -sha1 -sign "$c/key.pem" "dist/$name.jar" | base64 -w0)" >> "dist/$name.jad"
        echo "done (signed): dist/$name.jar ($(stat -c %s "dist/$name.jar") bytes)"
    else
        echo "done: dist/$name.jar ($(stat -c %s "dist/$name.jar") bytes)"
    fi
}
