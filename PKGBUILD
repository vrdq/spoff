# Maintainer: vrdq
pkgname=spoff
pkgver=0.1.0
pkgrel=1
pkgdesc="Fast, minimalist, dark-monochrome Spotify & YouTube music TUI with offline caching"
arch=('any')
url="https://github.com/vrdq/spoff"
license=('MIT')
depends=(
    'python'
    'python-textual'
    'python-rich'
    'yt-dlp'
    'mpv'
)
makedepends=(
    'python-build'
    'python-installer'
    'python-wheel'
    'python-hatchling'
)

prepare() {
    cd "$srcdir"
    if [ ! -f "pyproject.toml" ]; then
        if [ -f "$startdir/pyproject.toml" ]; then
            cp -a "$startdir/pyproject.toml" "$startdir/spoff" "$startdir/README.md" "$startdir/LICENSE" "$srcdir/"
        elif [ -d "$srcdir/$pkgname-$pkgver" ]; then
            cd "$srcdir/$pkgname-$pkgver"
        fi
    fi
}

build() {
    cd "$srcdir"
    if [ -d "$srcdir/$pkgname-$pkgver" ]; then
        cd "$srcdir/$pkgname-$pkgver"
    fi
    python -m build --wheel --no-isolation
}

package() {
    cd "$srcdir"
    if [ -d "$srcdir/$pkgname-$pkgver" ]; then
        cd "$srcdir/$pkgname-$pkgver"
    fi
    python -m installer --destdir="$pkgdir" dist/*.whl
    install -Dm644 LICENSE "$pkgdir/usr/share/licenses/$pkgname/LICENSE"
}
