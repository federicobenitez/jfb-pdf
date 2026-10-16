#!/usr/bin/env bash
# Empaqueta JFB Pdf como archivo .deb usando dpkg-deb.
#
# Uso: ./build.sh
# Resultado: jfbpdf_<version>_all.deb en el directorio raíz del proyecto.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PKG="jfbpdf"
VERSION="$(sed -n 's/^Version: //p' "$HERE/packaging/control" | head -n1)"

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

install -m 0644 "$HERE/packaging/control" "$STAGE/DEBIAN/control"

cp -a "$HERE/jfbpdf" "$STAGE/usr/share/jfbpdf/jfbpdf"

install -m 0755 "$HERE/bin/jfbpdf" "$STAGE/usr/bin/jfbpdf"

install -m 0644 "$HERE/packaging/jfbpdf.desktop" \
    "$STAGE/usr/share/applications/jfbpdf.desktop"
install -m 0644 "$HERE/packaging/jfbpdf.svg" \
    "$STAGE/usr/share/icons/hicolor/scalable/apps/jfbpdf.svg"
install -m 0644 "$HERE/packaging/copyright" \
    "$STAGE/usr/share/doc/$PKG/copyright"

if command -v gzip >/dev/null 2>&1; then
    gzip -9 -n -c "$HERE/packaging/changelog" > "$STAGE/usr/share/doc/$PKG/changelog.gz"
else
    install -m 0644 "$HERE/packaging/changelog" "$STAGE/usr/share/doc/$PKG/changelog"
fi

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
