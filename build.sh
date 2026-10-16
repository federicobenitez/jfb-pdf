#!/usr/bin/env bash
# Empaqueta JFB Pdf como archivo .deb usando dpkg-deb.
#
# La versión se toma de jfbpdf/version.py (fuente única de verdad) y se
# sustituye en las plantillas de packaging/.
#
# Uso: ./build.sh
# Resultado: jfbpdf_<version>_all.deb en el directorio raíz del proyecto.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PKG="jfbpdf"
VERSION="$(sed -n 's/^VERSION = "\(.*\)"$/\1/p' "$HERE/jfbpdf/version.py" | head -n1)"
if [[ -z "$VERSION" ]]; then
    echo "No se pudo determinar la versión desde jfbpdf/version.py" >&2
    exit 1
fi

STAGE="$HERE/deb"
OUT="$HERE/${PKG}_${VERSION}_all.deb"

echo "==> Limpiando staging previo: $STAGE"
rm -rf "$STAGE"

echo "==> Creando estructura del paquete"
install -d "$STAGE/DEBIAN"
install -d "$STAGE/usr/bin"
install -d "$STAGE/usr/share/jfbpdf"
install -d "$STAGE/usr/share/applications"
install -d "$STAGE/usr/share/icons/hicolor/scalable/apps"
install -d "$STAGE/usr/share/doc/$PKG"
install -d "$STAGE/usr/share/man/man1"

echo "==> Generando metadatos (versión $VERSION)"
sed "s/@VERSION@/$VERSION/g" "$HERE/packaging/control.in" > "$STAGE/DEBIAN/control"

if command -v gzip >/dev/null 2>&1; then
    sed "s/@VERSION@/$VERSION/g" "$HERE/packaging/jfbpdf.1" \
        | gzip -9 -n > "$STAGE/usr/share/man/man1/jfbpdf.1.gz"
    gzip -9 -n -c "$HERE/packaging/changelog" > "$STAGE/usr/share/doc/$PKG/changelog.gz"
else
    sed "s/@VERSION@/$VERSION/g" "$HERE/packaging/jfbpdf.1" \
        > "$STAGE/usr/share/man/man1/jfbpdf.1"
    install -m 0644 "$HERE/packaging/changelog" "$STAGE/usr/share/doc/$PKG/changelog"
fi

echo "==> Copiando la aplicación"
cp -a "$HERE/jfbpdf" "$STAGE/usr/share/jfbpdf/jfbpdf"
install -m 0755 "$HERE/bin/jfbpdf" "$STAGE/usr/bin/jfbpdf"

install -m 0644 "$HERE/packaging/jfbpdf.desktop" \
    "$STAGE/usr/share/applications/jfbpdf.desktop"
install -m 0644 "$HERE/packaging/jfbpdf.svg" \
    "$STAGE/usr/share/icons/hicolor/scalable/apps/jfbpdf.svg"
install -m 0644 "$HERE/packaging/copyright" \
    "$STAGE/usr/share/doc/$PKG/copyright"

echo "==> Normalizando permisos (legibles por todos los usuarios)"
find "$STAGE/usr/share/jfbpdf" -type d -exec chmod 0755 {} +
find "$STAGE/usr/share/jfbpdf" -type f -exec chmod 0644 {} +
find "$STAGE/usr/share/doc/$PKG" -type f -exec chmod 0644 {} +

echo "==> Eliminando posibles __pycache__"
find "$STAGE" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true

echo "==> Construyendo $OUT"
dpkg-deb --build --root-owner-group "$STAGE" "$OUT"

echo
echo "==> Paquete generado: $OUT"
dpkg-deb --info "$OUT" | sed -n '1,20p'
