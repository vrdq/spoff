# Maintainer: yassinMMK <y25mmk@gmail.com>
pkgname=spoff
pkgver=0.1.0
pkgrel=1
pkgdesc="Fast, minimalist, dark-monochrome Spotify & YouTube music TUI with offline caching"
arch=('any')
url="https://github.com/yassinMMK/spoff"
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

build() {
    python -m build --wheel --no-isolation
}

package() {
    python -m installer --destdir="$pkgdir" dist/*.whl
    install -Dm644 LICENSE "$pkgdir/usr/share/licenses/$pkgname/LICENSE"
}
